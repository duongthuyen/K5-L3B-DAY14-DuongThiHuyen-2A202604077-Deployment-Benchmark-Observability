# Benchmark & Observability Report — Day 14

**Họ tên:** Dương Thị Huyền
**Ngày chạy:** 2026-11-26
**Môi trường:** Windows 11 Pro + WSL2 Ubuntu 24.04 (Python 3.12.3), Docker Desktop (Engine 29.9.3, Compose v5.7.0). Load generator và agent chạy trên cùng một máy. Stack production: mỗi container agent giới hạn `cpus: 0.5`, `memory: 256M`.

File số liệu: `reports/*.json` (load test + eval, develop), `benchmark-results/scale_{1,3}_stats.csv` (Locust, production), ảnh trong `screenshots/`.

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500 ms** (develop `load_test.py`); < **1 s** cho alert ở production | Mock LLM mất ~150–250 ms/call nên request bình thường có p95 ≈ 220 ms; 500 ms chừa gấp đôi cho jitter và hàng đợi. Alert production dùng 1 s (`AgentHighLatencyP95`) để không báo động giả khi có vài request "slow" |
| Error rate `/ask` | < **2 %** (develop); alert khi > **5 %** trong 1 phút | Mock có ~1 % lỗi upstream ngẫu nhiên (`MOCK_ERROR_RATE=0.01`). 2 % là error budget cho phép, 5 % là mức chắc chắn có sự cố |
| Accuracy trên golden dataset | ≥ **85 %** (quality gate CI), mục tiêu 100 % cho bản release | Golden dataset chỉ 23 câu, mỗi câu ≈ 4.3 điểm %; 85 % cho phép sai tối đa 3 câu. Bản release phải không có regression so với baseline |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

Lệnh: `python load_test.py --url http://localhost:8002 --requests 300 --concurrency <c> --out reports/load_v2_c<c>.json` (agent v2, một process uvicorn).

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.08 | 191.5 | 221.0 | 1714.9 | 1.00 % (3 × 503) | ✅ PASS |
| 10 | 40.33 | 189.3 | 221.8 | 1680.1 | 0.33 % (1 × 503) | ✅ PASS |
| 50 | 103.01 | 204.5 | 506.6 | 1709.4 | 0.67 % (2 × 503) | ❌ FAIL (p95 > 500) |
| 100 | 58.28 | 390.6 | 3113.4 | 3277.6 | 0.67 % (2 × 503) | ❌ FAIL (p95 > 500) |

**Nhận xét:** RPS tăng gần **tuyến tính** từ 1 → 10 (4.08 → 40.33, ×9.9) vì mock LLM là `asyncio.sleep`, các request chỉ "chờ" song song nên RPS ≈ concurrency / latency (10 / 0.22 s ≈ 45). Từ 10 → 50 RPS chỉ tăng ×2.6 và p95 bắt đầu vượt SLO: process Python đơn luồng bắt đầu bão hoà CPU (parse JSON, pydantic, ghi log, metrics, cộng thêm client `httpx` chạy cùng máy). Ở 100, RPS **giảm** còn 58.28 và p50 tăng gấp đôi (390.6 ms): request phải xếp hàng trong event loop, thời gian chờ hàng đợi lớn hơn thời gian xử lý thật (p95 3.1 s). Đây là dấu hiệu quá tải: thêm concurrency không thêm throughput mà chỉ thêm latency. Muốn phục vụ > ~100 RPS thì phải scale ngang (nhiều worker/instance), không phải tăng concurrency.

### 2.2 Tail latency

Cùng `--requests 300 --concurrency 20`, khởi động lại v2 với từng giá trị `MOCK_SLOW_RATE` (request "slow" bị cộng thêm 1500 ms).

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 193.3 | 218.8 | 235.0 |
| 0.03 (mặc định) | 252.6 | 237.2 | 1704.7 |
| 0.1 | 301.4 | 1680.7 | 1706.3 |

**Nhận xét:** **p99** nhạy nhất. Với 3 % request chậm, avg chỉ tăng 30 % (193 → 253 ms) và p95 gần như không đổi, nhưng p99 tăng **7 lần** (235 → 1705 ms), vì percentile 99 rơi đúng vào nhóm 3 % chậm. Khi tỉ lệ chậm lên 10 % (> 5 %), p95 cũng nhảy lên 1681 ms trong khi avg chỉ 301 ms. Average trộn 97 request nhanh với 3 request chậm thành một con số "trông ổn", che mất việc 3 % người dùng chờ gần 2 giây. Vì vậy SLO đặt theo p95/p99: nó nói thẳng "x % người dùng có trải nghiệm tệ hơn ngưỡng". Ngoài ra RPS cũng giảm (99.5 → 52.6) vì 300 request ngắn nên thời gian chạy bị kéo dài bởi đuôi request chậm.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3 % (1/23) | 91.3 % (21/23) | **100.0 % (23/23)** |
| Avg tokens / câu | 57.5 | 109.3 | 116.6 |
| Tổng cost (USD) | 0.000409 | 0.000764 | 0.000816 |
| Cost / câu đúng (USD) | 0.00040935 | 0.00003639 | 0.00003547 |
| Quality gate | ❌ FAIL (≥ 80 %) | ✅ PASS (≥ 80 %, 0 regression) | ✅ PASS (≥ 100 %, 0 regression so với v2) |

Lệnh eval cuối: `python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json` → `Quality gate (accuracy ≥ 100%, không regression): ✅ PASS`, exit code 0, "Câu được cải thiện: 2, Câu bị regression: 0".

**Những câu v2 trả lời sai ban đầu và cách sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| security-02 ("JWT khác gì API key…") | `token`, `hết hạn`, `chữ ký` | Thêm tài liệu kích hoạt bởi `["jwt", "json web token"]` vào `KNOWLEDGE_BASE`: API key là chuỗi cố định không tự hết hạn; JWT là token có chữ ký số, chứa user/role/thời điểm hết hạn (exp) |
| scaling-03 ("Kubernetes HPA scale dựa trên…") | `cpu`, `memory`, `custom metrics` | Thêm tài liệu kích hoạt bởi `["hpa", "horizontal pod autoscaler", "kubernetes", "autoscal"]`: HPA scale số pod theo CPU, memory hoặc custom metrics (RPS, queue length, latency) |

Trước khi sửa, mình kiểm tra các từ khoá mới chỉ xuất hiện trong đúng 2 câu hỏi này của golden dataset (không "cướp" retrieval của câu khác) → không regression. Không sửa `golden_dataset.jsonl`. Bản sửa được chép sang `production/utils/mock_llm.py`.

**Quality gate trong CI:** [.github/workflows/eval.yml](.github/workflows/eval.yml) chạy agent v2 rồi `run_eval.py --min-accuracy 0.85 --baseline reports/eval_v2_fixed.json`. `run_eval.py` trả **exit code 1** khi accuracy < ngưỡng hoặc có câu regression → step fail → job fail → bật branch protection "Require status checks to pass" thì PR không merge được. Đã kiểm tra: chạy gate 0.85 trên v1 → `exit code 1`, trên v2 đã sửa → `exit code 0`.

**Trade-off v1 vs v2:** v2 tốn gần **gấp đôi token/câu** (57.5 → 109.3, do prompt kèm tài liệu retrieve) và thêm ~20 ms retrieval, tổng cost tăng 87 %. Nhưng accuracy từ 4.3 % lên 91.3 % nên **cost / câu đúng giảm ~11 lần** (0.00041 → 0.000036 USD). So theo "cost / câu" thì v1 rẻ hơn, nhưng câu trả lời sai không có giá trị, nên "cost / câu đúng" mới là chỉ số đúng để so hai phiên bản. Sửa KB tăng thêm 7.3 token/câu nhưng đưa accuracy lên 100 % và cost/câu đúng còn giảm nhẹ.

---

## 4. Observability stack (production)

Bật stack: `docker compose -p k5-day14-observability up -d --build` (2 agent sau Nginx, Prometheus, Grafana, Jaeger, Locust). Tải nền khi làm 7.4–7.6: Locust headless 30 user, ramp 5/s.

> Hai chỗ mình phải sửa để stack chạy đúng:
> 1. `production/Dockerfile`: container agent crash `ModuleNotFoundError: No module named 'uvicorn'` vì user `agent` có home `/app` nên user-site mặc định là `/app/.local`, không phải `/home/agent/.local` nơi đã copy package. Thêm `ENV PYTHONUSERBASE=/home/agent/.local`.
> 2. `docker compose run locust` tự start dependency (nginx → agent) theo `deploy.replicas: 2`, nên lần đo "1 instance" thực tế có 2 agent. Thêm `--no-deps` và ghi lại số container agent trước/sau mỗi lần đo (1 và 3, đúng như mong muốn).

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: [screenshots/grafana-dashboard.png](screenshots/grafana-dashboard.png) (30 user; dải 15 phút thấy cả các thí nghiệm C và E)
- [x] Trace chậm trong Jaeger: [screenshots/jaeger-slow-trace.png](screenshots/jaeger-slow-trace.png)
- [x] Trace lỗi trong Jaeger, có event `exception`: [screenshots/jaeger-error-trace.png](screenshots/jaeger-error-trace.png)
- [x] Trang Alerts của Prometheus với 2 alert cùng **firing** (`AgentHighErrorRate`, `AgentHighLatencyP95`): [screenshots/prometheus-alert-firing.png](screenshots/prometheus-alert-firing.png)

**Exercise 7.4 — số đo baseline (v2, 2 instance, 30 user), query PromQL lúc 15:17:09:**

| Chỉ số | Giá trị |
|---|---|
| p95 `/ask` | 0.291 s |
| avg `/ask` | 0.221 s |
| p99 `/ask` | 1.748 s |
| Req/s theo instance | 9.41 / 9.31 (172.19.0.5 / 172.19.0.8) |
| Error rate | 1.04 % |
| Tokens/s | input 1459.7, output 771.3 |
| Cost USD/giờ | 4.22 |

1. **p95 vs avg:** p95 = 291 ms, avg = 221 ms, chênh 70 ms. Phần lớn request nằm sát nhau (~200 ms); nhưng p99 = 1.75 s cho thấy có một đuôi nhỏ (~3 % request "slow" thêm 1.5 s). Khoảng cách avg ↔ p99 mới là tail latency; avg không cho thấy đuôi này.
2. **Requests/s theo instance:** 9.41 vs 9.31 req/s, gần như chia đều (lệch ~1 %). Nginx dùng round-robin; độ lệch nhỏ do thời gian xử lý từng request khác nhau.
3. **Input tokens/s > output tokens/s** (1460 vs 771): input = system prompt + tài liệu retrieve (RAG) + câu hỏi, còn output chỉ là câu trả lời ngắn. Với agent v2, prompt luôn dài hơn câu trả lời. (Giá output đắt hơn input 4 lần nên về cost thì output vẫn chiếm phần lớn.)
4. **`curl http://localhost:8080/metrics` → `HTTP/1.1 404 Not Found`** từ `nginx/1.28.3`. Nginx chỉ proxy `/ask`, `/health`… ra ngoài và cố ý không public `/metrics`: metrics lộ thông tin nội bộ (route, số request, cost, version) và cho phép người ngoài spam endpoint đó. Prometheus scrape thẳng `agent:8000/metrics` trong network nội bộ `obs_net`.

**Đáng chú ý:** ngay ở baseline 30 user, `LLMCostBurnRateHigh` đã **firing** (4.22 USD/giờ > ngưỡng 2 USD/giờ). Ngưỡng $2/giờ trong `alert_rules.yml` thấp hơn mức chi tiêu bình thường của tải này. Trong production nên đặt ngưỡng theo budget thực tế, hoặc dạng burn-rate so với budget ngày (vd chi > 2× tốc độ cho phép), nếu không alert sẽ luôn đỏ và người trực bỏ qua nó.

**`promtool test rules`:** `Processing test file: /p/alert_rules_test.yml  SUCCESS`.

### 4.2 Thí nghiệm "phá hệ thống"

Đổi cấu hình bằng `.env` rồi `docker compose -p k5-day14-observability up -d --scale agent=2` (container agent được tạo lại). Thời gian đo bằng script poll `/api/v1/alerts` mỗi 5 s, tính từ lúc chạy lệnh đổi cấu hình (đã gồm thời gian recreate container và độ trễ scrape 5 s).

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (error rate đo được 23.2 %) | **pending ở 33.7 s → firing ở 96.3 s** (`for: 1m`). Lần chạy 1: pending 28.5 s → firing 91.2 s (17.3 %) | Error rate (đỏ), Requests/s theo status (503 tăng), LLM calls theo kết quả (error) |
| B | `MOCK_SLOW_RATE=0.3` (chạy cùng lúc với A) | `AgentHighLatencyP95` (p95 1.90 s, avg chỉ 0.59 s) | **pending ở 27.4 s → firing ở 147.6 s** (`for: 2m`). Lần chạy 1: pending 28.5 s → firing 147.6 s (p95 1.89 s, p99 1.98 s, avg 0.57 s) | Latency p50/p95/p99 (p95 & p99 vượt vạch 1 s, avg tăng ít hơn) || C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (đã firing sẵn ở baseline, xem 4.1; sau khi đổi model alert reset vì series mới, pending ở 4.4 s → firing ở **144.5 s**) | 144.5 s | Cost burn rate: 2.06 → **19.18 USD/giờ** (×9.3); LLM latency p95 theo model (large ~2.5× chậm hơn), Latency p95 0.71 s |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` (`up{instance="172.19.0.5:8000"} = 0`) | **pending ở 11.5 s → firing ở 42.8 s** (`for: 30s`) | Instances up (2 → 1). Error rate của agent vẫn ~1.1 % (xanh): request kẹt ở instance bị pause không được agent nào đếm |
| E | `AGENT_VERSION=v1` | **Không có alert mới** trong 180 s (chỉ còn `LLMCostBurnRateHigh` từ C) | — | Tokens/s giảm (input 1460 → 648/s, output 771 → 354/s), Retrieval hit ratio mất, bảng "Phiên bản đang chạy" đổi sang v1. Error rate 0.79 %, p95 0.199 s: các panel hạ tầng đều **xanh hơn** |

Ảnh `prometheus-alert-firing.png` chụp ở lần chạy 2 của A+B (đổi cấu hình lúc 15:45:19, cả 2 alert firing lúc 15:47:46): nhóm `agent-slo` hiện **FIRING (2)**.

Ghi chú khi đổi cấu hình: mỗi lần recreate container, `AgentInstanceDown` lên **pending** vài giây (target cũ biến mất / target mới chưa sẵn sàng) nhưng không tới firing nhờ `for: 30s`. Đây là ví dụ vì sao alert cần `for`.

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**

Đây là sự cố **chất lượng**, không phải hạ tầng: v1 trả lời nhanh hơn, rẻ hơn, ít lỗi hơn nên mọi metric Golden Signals đều đẹp hơn, nhưng accuracy rơi từ 100 % xuống 4.3 %. Cách phát hiện:
1. **Chặn trước khi deploy:** quality gate eval trong CI/CD (`run_eval.py --min-accuracy 0.85 --baseline …` trả exit 1 với v1), đúng như job `.github/workflows/eval.yml`.
2. **Giám sát chất lượng khi chạy:** chạy eval định kỳ (synthetic probe, vd mỗi 15 phút) trên golden dataset với bản đang chạy, xuất accuracy thành metric (`agent_eval_accuracy`) và alert khi giảm; theo dõi proxy metric như **retrieval hit ratio** (về 0 khi v1) và tokens/câu (giảm đột ngột) bằng alert "thay đổi bất thường so với tuần trước".
3. **Tín hiệu từ người dùng:** thumbs up/down, tỉ lệ hỏi lại; LLM-as-judge chấm mẫu một phần traffic thật.
4. **Theo dõi version:** alert/annotation khi `agent_info{version}` thay đổi để đối chiếu với các chỉ số chất lượng.

### 4.3 Debug bằng trace

Trace chậm nhất trong ~2 000 trace `POST /ask` của 2 phút tải (30 user, cấu hình mặc định):

- `trace_id`: `18f1e5f86ab6d7629b50759894717287`
- Tổng thời gian: **3059.7 ms**
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` (3039.8 ms, 99.4 %)**. Các span còn lại: `agent.run` 3055.8 ms (span cha), `agent.retrieve_context` 15.7 ms (0.5 %). Attributes: `gen_ai.request.model=mock-llm-small`, `gen_ai.usage.input_tokens=79`, `output_tokens=41`.
- Kết luận: chậm nằm ở **LLM call** (rơi vào nhóm request "slow" của mock), không phải ở retrieval hay code của agent → hướng xử lý là timeout + retry/hedging cho LLM call, hoặc fallback model, chứ không phải tối ưu code.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep 18f1e5f86ab6d7629b50759894717287`):

```json
{"ts": "2026-11-26T08:39:40.021673+00:00", "level": "INFO", "service": "observable-agent", "instance": "32cfab136dd9", "event": "llm_call", "trace_id": "18f1e5f86ab6d7629b50759894717287", "span_id": "13c6e3587d7e45f3", "request_id": "4585f326e9da", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 79, "output_tokens": 41, "cost_usd": 3.6e-05, "llm_ms": 1683.4}
{"ts": "2026-11-26T08:39:40.022541+00:00", "level": "INFO", "service": "observable-agent", "instance": "32cfab136dd9", "event": "http_request", "trace_id": "18f1e5f86ab6d7629b50759894717287", "span_id": "13c6e3587d7e45f3", "request_id": "4585f326e9da", "method": "POST", "route": "/ask", "status": 200, "ms": 1702.9}
```

Nhận xét: log ghi `llm_ms: 1683.4`, là độ trễ **mô phỏng** mà mock định ra (120 ms + jitter + 1500 ms slow). Span đo thời gian thực tế 3039.8 ms. Phần chênh ~1.36 s là thời gian coroutine chờ được event loop chạy lại: container agent bị giới hạn 0.5 CPU và đang xử lý ~10 req/s nên bị throttle. Trace đo wall-clock thật nên phát hiện được độ trễ do bão hoà CPU mà con số tự báo cáo trong log không thấy.

Trace lỗi: `trace_id` `238e2774495620f7ec4ae2705ae9c429` (93.2 ms, HTTP 503). Span `chat mock-llm-small` có status ERROR, attribute `error.type=upstream_unavailable` và event `exception` (`exception.type=utils.mock_llm.LLMError`, `exception.message=Upstream LLM error: 503 Service Unavailable (mock)`, có stacktrace). Log cùng trace_id:

```json
{"ts": "2026-11-26T08:39:22.879301+00:00", "level": "INFO", "service": "observable-agent", "instance": "32cfab136dd9", "event": "llm_error", "trace_id": "238e2774495620f7ec4ae2705ae9c429", "span_id": "e37ca6c1d1a28d11", "request_id": "dff853b06ec5", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

Lệnh: `docker compose -p k5-day14-observability run --rm --no-deps locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_<n>`. Số liệu dòng `POST /ask` trong `benchmark-results/scale_{1,3}_stats.csv`.

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 56.69 | 200 | 680 | 1700 | 63 / 6969 (0.90 %) |
| 3 | 57.36 | 200 | 240 | 1700 | 63 / 7118 (0.89 %) |

**Giải thích kết quả:** Scale 1 → 3 **cải thiện rõ p95** (680 → 240 ms, −65 %) và avg (279 → 235 ms), nhưng **RPS gần như không đổi** (56.7 → 57.4) còn **p99 giữ nguyên 1700 ms**.
- RPS không tăng vì nó bị giới hạn bởi **phía tải**, không phải phía server: 100 user Locust, mỗi user nghỉ 0.5–2 s giữa các lần gọi (`wait_time = between(0.5, 2)`), nên tối đa ≈ 100 / (1.25 s + latency) ≈ 60 req/s (`/ask` + `/health`). Server 1 instance vẫn phục vụ đủ lượng này, chỉ là chậm hơn.
- p95 giảm vì 1 instance (0.5 CPU) bị bão hoà ở ~57 req/s: request xếp hàng trong event loop → đuôi phân phối kéo dài. 3 instance chia tải mỗi cái ~19 req/s nên hết xếp hàng, p95 về sát thời gian xử lý thuần (~220 ms).
- p99 không đổi vì nút thắt của đuôi là **LLM** (3 % request mock "slow" +1.5 s). Thêm instance không làm LLM nhanh hơn (panel "LLM latency p95 theo model" giữ nguyên). Muốn giảm p99 phải xử lý ở tầng LLM: timeout + retry/hedged request, cache câu trả lời, model nhanh hơn, hoặc streaming để giảm thời gian tới token đầu.
- Failures giống nhau (63 lần 503 ≈ 0.9 %) vì đó là lỗi upstream mô phỏng (`MOCK_ERROR_RATE=0.01`), không liên quan tới số instance.

---

## 6. Kết luận

- 3 điều học được về benchmark & observability:
  1. **Percentile quan trọng hơn trung bình.** 3 % request chậm làm p99 tăng 7× trong khi avg chỉ tăng 30 %. SLO và alert phải đặt theo p95/p99, và phải xem cả p99 vì có loại chậm (LLM) mà scale ngang không chữa được.
  2. **Metric hạ tầng không thấy lỗi chất lượng, cũng không thấy lỗi ngoài service.** Đổi sang v1 làm dashboard xanh hơn dù accuracy rơi còn 4.3 %. Pause 1 instance thì error rate của agent vẫn xanh. Cần thêm eval/quality gate, giám sát ở load balancer và synthetic check từ ngoài.
  3. **Đo phải kiểm chứng được và đúng cấu hình.** Mình suýt ghi sai benchmark "1 instance" vì `docker compose run` âm thầm bật lại 2 agent; trace (wall-clock) cũng cho số khác log tự báo cáo (thời gian mô phỏng). Mỗi số trong báo cáo cần đối chiếu với nguồn thô (CSV/JSON, `docker ps`, trace).
- Nếu đưa agent này lên production thật, mình sẽ thêm:
  - Metric `agent_eval_accuracy` từ eval định kỳ + alert khi giảm; alert khi **retrieval hit ratio** < 80 %.
  - Alert **cost theo budget** (burn rate so với `DAILY_BUDGET_USD`, multi-window 1h/6h) thay cho ngưỡng cố định $2/giờ (đang firing ngay ở tải bình thường).
  - Metric và alert ở **Nginx** (5xx/504, upstream response time) + synthetic probe từ bên ngoài để bắt lỗi kiểu thí nghiệm D.
  - Alert **saturation**: `agent_in_flight_requests` cao kéo dài và CPU throttling của container, làm tín hiệu để autoscale trước khi p95 vỡ.
  - Multi-window multi-burn-rate alert cho error budget (SLO 99.5 %/30 ngày) thay cho ngưỡng 5 % đơn lẻ.
