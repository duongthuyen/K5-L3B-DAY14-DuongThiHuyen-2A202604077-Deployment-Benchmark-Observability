# Day 14 Lab — Mission Answers

> Môi trường chạy: Windows 11 + WSL2 Ubuntu 24.04 (Python 3.12.3, `os.name == "posix"`), Docker Desktop (Engine 29.9.3, Compose v5.7.0).
> Mọi output bên dưới là output thật của các lệnh đã chạy ngày 2026-11-26. Token JWT đã được che (`eyJ***REDACTED***`).

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Dòng | Anti-pattern | Hậu quả |
|---|---|---|---|
| 1 | 17–18 | Hardcode secret: `OPENAI_API_KEY = "sk-hardcoded-..."`, `DATABASE_URL = "postgresql://admin:password123@..."` | Push lên GitHub là lộ key + mật khẩu DB, phải revoke; secret nằm mãi trong lịch sử git |
| 2 | 34 | Log ra secret: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | Key xuất hiện trong log của platform (ai xem log cũng thấy). Đã thấy thật trong log khi chạy: `[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this` |
| 3 | 33, 38 | Dùng `print()` thay vì logging có level/format | Không lọc theo level, không parse được bằng log aggregator, không có request_id/timestamp |
| 4 | 42–43 | Không có health check endpoint | Platform không biết agent chết để restart. Đã kiểm tra: `GET /health` → **404 Not Found** |
| 5 | 51 | `host="localhost"` | Trong container chỉ lắng nghe loopback → từ ngoài container/LB không gọi được |
| 6 | 52 | `port=8000` cố định, không đọc `PORT` | Railway/Render/Cloud Run cấp `PORT` động → app không nhận traffic, healthcheck fail |
| 7 | 21, 53 | `DEBUG = True`, `reload=True` | Reloader theo dõi file, tốn tài nguyên, có thể lộ stack trace; không dùng cho production |
| 8 | 20–22 | Config cứng trong code (`MAX_TOKENS = 500`, `DEBUG`) | Muốn đổi cấu hình giữa dev/staging/prod phải sửa code và build lại |
| 9 | — | Không xử lý shutdown (không lifespan, không SIGTERM) | Khi platform tắt/redeploy, request đang chạy có thể bị cắt ngang |

### Exercise 1.2: Chạy bản develop

```text
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found
{"detail":"Not Found"}
```

Log server (thấy rõ secret bị in ra):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.
```

**Quan sát:** chạy được, nhưng chưa production-ready: lộ secret trong log, không có `/health`, chỉ nghe `localhost`.

### Exercise 1.3: So sánh develop vs production

Kết quả chạy bản production (`cp .env.example .env && python app.py`):

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","model":"gpt-4o-mini"}

$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":3.3,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T07:28:40.818862+00:00"}

$ curl http://localhost:8000/ready
{"ready":true}
```

Gửi SIGTERM → log: `Shutting down → Waiting for application shutdown → Application shutdown complete → Finished server process`.

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`OPENAI_API_KEY`, `DEBUG`, port) | `config.py` đọc env (`os.getenv("PORT")`, `AGENT_API_KEY`…), `validate()` fail-fast khi production thiếu `AGENT_API_KEY` | Cùng một image chạy được ở dev/staging/prod; secret không nằm trong git (12-Factor III) |
| Secrets | `sk-...`, `password123` trong code và bị in ra log | Đọc từ env, chỉ log `question_length`, không log secret | Lộ key = mất tiền/mất dữ liệu; phải revoke |
| Health check | Không có (`/health` → 404) | `/health` (liveness: uptime, version) + `/ready` (readiness: 503 khi chưa sẵn sàng) | Platform dùng `/health` để restart, LB dùng `/ready` để quyết định route traffic |
| Logging | `print()` | `logging` với format JSON, có event name | Log aggregator (Loki, Datadog) parse/lọc được; có level |
| Shutdown | Đột ngột, không lifespan | `lifespan` dọn dẹp + handler SIGTERM, uvicorn chờ request xong | Redeploy/scale-in không làm rớt request đang chạy |
| Host/Port | `localhost:8000` cố định, `reload=True` | `0.0.0.0`, `PORT` từ env, reload chỉ khi `DEBUG=true` | Container/cloud cần bind `0.0.0.0` và theo `PORT` platform cấp |

**Điều mình tự phát hiện khi chạy bản production:** log JSON trong `app.py` thực tế **không được áp dụng**. Log in ra `WARNING:root:OPENAI_API_KEY not set — using mock LLM` (format mặc định) và các dòng `logger.info(json.dumps(...))` không xuất hiện. Lý do: `config.py` gọi `logging.warning(...)` trong `validate()` lúc import, trước `logging.basicConfig(...)` của `app.py` → root logger đã có handler mặc định (level WARNING) nên `basicConfig` sau đó không có tác dụng. Ngoài ra format `'{"msg":"%(message)s"}'` bọc một chuỗi JSON vào trong chuỗi JSON nên ra JSON không hợp lệ (thấy rõ ở Part 2: `"msg":"{"event": "request", ...}"`). Ở Part 6 mình tự viết `JsonFormatter` dùng `json.dumps` cho cả dòng để tránh lỗi này.

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11` — bản Python đầy đủ trên Debian (có gcc, header, nhiều tool), rất nặng.
2. **Working directory:** `/app` (`WORKDIR /app`), mọi lệnh `COPY`/`RUN`/`CMD` sau đó chạy tương đối trong thư mục này.
3. **Vì sao COPY `requirements.txt` trước:** Docker cache theo layer. Dependencies ít thay đổi, code thay đổi liên tục. Copy `requirements.txt` → `pip install` trước, rồi mới `COPY app.py` thì khi chỉ sửa code, layer `pip install` (53.3 MB, tốn thời gian nhất) được dùng lại từ cache. Nếu `COPY . .` trước thì mỗi lần sửa code đều cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số **mặc định**, bị ghi đè hoàn toàn khi chạy `docker run image <lệnh khác>`. `ENTRYPOINT` là executable **cố định** của container; tham số trong `docker run image <args>` được nối thêm vào sau ENTRYPOINT (muốn đổi phải dùng `--entrypoint`). Hay dùng kết hợp: `ENTRYPOINT ["uvicorn"]` + `CMD ["app:app", "--port", "8000"]`. Dùng exec form (`["..."]`) để process là PID 1 và nhận được SIGTERM.

### Exercise 2.2: Build & run bản develop

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -p 8000:8000 my-agent:develop
$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker?"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":3.8,"container":true}
$ docker logs p2dev
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     172.17.0.1:34366 - "POST /ask?question=What%20is%20Docker? HTTP/1.1" 200 OK
```

### Exercise 2.3: Multi-stage build & so sánh kích thước

- **Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc`, `libpq-dev` (build tools) rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này **không** được deploy.
- **Stage 2 (`runtime`, `python:3.11-slim`):** tạo user non-root `appuser`, chỉ `COPY --from=builder /root/.local` (site-packages đã cài) + `main.py` + `utils/`, có `HEALTHCHECK`, chạy `uvicorn --workers 2`.
- **Vì sao nhỏ hơn:** image cuối không chứa gcc/header/apt cache/pip cache của stage builder, và dùng base `slim` thay cho base full.

Số đo (`docker images` + `docker image inspect -f '{{.Size}}'`):

```text
REPOSITORY   TAG        SIZE
my-agent     advanced   240MB
my-agent     develop    1.67GB

my-agent:develop  1667771676 bytes
my-agent:advanced  239815554 bytes
```

- Develop: **1.67 GB** (1 667.8 MB)
- Production (advanced): **240 MB** (239.8 MB)
- Chênh lệch: **giảm 85.6 %** (`(1667.77 − 239.82) / 1667.77`), tức nhỏ hơn ~7 lần.

`docker history my-agent:develop` cho thấy layer `pip install` chỉ 53.3 MB; phần còn lại (~1.6 GB) là base image `python:3.11` đầy đủ, nên đổi base sang `slim` + multi-stage là chỗ giảm dung lượng chính.

### Exercise 2.4: Docker Compose stack (`02-docker/production`)

```text
                 ┌──────────────┐
  client ──:80──▶│ nginx:alpine │  (port duy nhất mở ra host: 80, 443)
                 └──────┬───────┘
                        │ proxy_pass http://agent_backend (agent:8000)
            network "internal" (bridge)
                        ▼
                 ┌──────────────┐
                 │ agent (2 wk) │  build từ 02-docker/production/Dockerfile, target runtime
                 └──┬────────┬──┘
     REDIS_URL      │        │   QDRANT_URL
   redis://redis:6379/0      http://qdrant:6333
                    ▼        ▼
            ┌───────────┐  ┌──────────────┐
            │ redis:7   │  │ qdrant v1.9  │   (volume redis_data, qdrant_data)
            └───────────┘  └──────────────┘
```

- Service được start: `redis`, `qdrant`, `agent`, `nginx`. Agent `depends_on` redis + qdrant với `condition: service_healthy` nên chỉ start khi 2 service kia healthy; nginx start sau agent.
- Giao tiếp: các container cùng network `internal`, gọi nhau bằng **tên service** qua Docker DNS (`agent:8000`, `redis:6379`, `qdrant:6333`). Agent không publish port; chỉ Nginx mở cổng 80 ra host → mọi traffic đi qua reverse proxy (có security header, rate limit 10 r/s/IP).

```text
$ docker compose -p k5-day14-docker up -d --build
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 13 seconds (healthy)   8000/tcp
nginx     nginx:alpine            Up 13 seconds             0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 25 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 24 seconds (healthy)   6379/tcp

$ curl -i http://localhost/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
{"status":"ok","uptime_seconds":11.5,"version":"2.0.0","timestamp":"2026-11-26T07:45:21.330596"}

$ curl -i -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

Header `Server: nginx` và các security header chứng tỏ request đi qua Nginx rồi mới tới agent. Debug container: `docker compose -p k5-day14-docker logs agent`, `docker exec -it <container> /bin/sh`.

---

## Part 3: Cloud Deployment

_(Điền sau CP6 — xem bên dưới mục "Part 3")_

---

## Part 4: API Security

### Exercise 4.1: API Key (`04-api-gateway/develop`, chạy bằng `AGENT_API_KEY=secret-key-123 python app.py`)

- **Key được check ở đâu:** hàm dependency `verify_api_key()` dùng `APIKeyHeader(name="X-API-Key", auto_error=False)`, gắn vào `/ask` bằng `Depends(verify_api_key)`. Key đúng lấy từ env `AGENT_API_KEY`.
- **Sai key:** thiếu header → **401** `Missing API key`; có header nhưng sai → **403** `Invalid API key`.
- **Rotate key:** đặt giá trị mới cho `AGENT_API_KEY` trên platform rồi restart/redeploy (không sửa code). Muốn không downtime thì cho server chấp nhận tạm 2 key (cũ + mới), cập nhật client sang key mới rồi bỏ key cũ.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: secret-key-123" -H "Content-Type: application/json" -d '{"question": "Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

### Exercise 4.2: JWT (`04-api-gateway/production`)

**JWT flow (`auth.py`):** `POST /auth/token` nhận username/password → `authenticate_user()` kiểm tra → `create_token()` ký payload `{sub, role, iat, exp=+60 phút}` bằng HS256 với `JWT_SECRET` → client gửi `Authorization: Bearer <token>` → `verify_token()` (`HTTPBearer`) `jwt.decode` kiểm tra chữ ký + hạn → trả `{username, role}` cho endpoint. Server không cần tra DB mỗi request (stateless). Token hết hạn → 401, token sai chữ ký → 403.

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username": "student", "password": "demo123"}'
HTTP/1.1 200 OK
{"access_token":"eyJ***REDACTED***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ***REDACTED***"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question": "Explain JWT"}'
HTTP/1.1 200 OK
x-content-type-options: nosniff
x-frame-options: DENY
{"question":"Explain JWT","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","usage":{"requests_remaining":9,"budget_remaining_usd":1.6e-05}}
```

### Exercise 4.3: Rate limiting (`rate_limiter.py`)

- **Algorithm:** *sliding window log* — mỗi user một `deque` chứa timestamp các request; mỗi lần gọi xoá timestamp cũ hơn 60 giây rồi đếm. Đủ `max_requests` → 429 kèm `Retry-After`, `X-RateLimit-*`.
- **Limit:** user thường **10 request / 60 giây** (`rate_limiter_user`), admin **100 request / 60 giây** (`rate_limiter_admin`).
- **Bypass cho admin:** không bỏ hẳn limit mà chọn limiter theo `role` lấy từ JWT: `limiter = rate_limiter_admin if role == "admin" else rate_limiter_user` (tài khoản `teacher` có role `admin`).
- **Hạn chế:** state nằm trong RAM của process → mỗi worker/instance có bộ đếm riêng và mất khi restart. Part 6 mình chuyển sang Redis sorted set.

Vòng lặp 20 request với token của `student` (request ở 4.2 đã dùng 1 lượt, nên lượt 10 trở đi bị chặn):

```text
Request  1 -> 200  ... "requests_remaining":8 ...
Request  2 -> 200  ... "requests_remaining":7 ...
...
Request  9 -> 200  ... "requests_remaining":0 ...
Request 10 -> 429  {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
Request 11 -> 429  ...
...
Request 20 -> 429  {"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" ... -d '{"question": "Test 21"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795679247
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}

$ curl http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
{"user_id":"student","date":"2026-11-26","requests":10,"input_tokens":40,"output_tokens":304,"cost_usd":0.000188,"budget_usd":1.0,"budget_remaining_usd":0.999812,"budget_used_pct":0.0}
```

### Exercise 4.4: Cost guard

`04-api-gateway/production/cost_guard.py` (bản mẫu) đếm token theo user trong **RAM**: `check_budget()` trước khi gọi LLM (per-user ≥ $1/ngày → **402**, global ≥ $10/ngày → 503), `record_usage()` sau khi gọi để cộng token và cost.

Cách mình làm (Part 6, `06-lab-complete/app/cost_guard.py`) — lưu trong **Redis** để mọi worker/instance dùng chung và không mất khi restart:

1. `check_budget()` gọi **trước** LLM: `MGET cost:daily:<YYYY-MM-DD> cost:monthly:<YYYY-MM>`; nếu tổng ngày ≥ `DAILY_BUDGET_USD` **hoặc** tổng tháng ≥ `MONTHLY_BUDGET_USD` → `HTTPException(402)`.
2. `record_cost(input_tokens, output_tokens)` gọi **sau** LLM: `cost = in/1000 × 0.00015 + out/1000 × 0.0006`, rồi trong một transaction `INCRBYFLOAT` cả key ngày và key tháng, `EXPIRE` 2 ngày / 32 ngày → **tự reset** khi sang ngày/tháng mới vì tên key đổi theo ngày (UTC).
3. Ngân sách đọc từ env nên đổi được không cần sửa code; script chấm đặt `DAILY_BUDGET_USD=0.000001` → request thứ 1 đã trả 402.

Tương đương lời giải mẫu `check_budget(user_id, estimated_cost)` với `$10/tháng`: key `budget:<user_id>:<YYYY-MM>`, `current + estimated_cost > 10` → False, ngược lại `INCRBYFLOAT` + `EXPIRE 32 ngày`.

**Vì sao cần cả rate limit lẫn cost guard:** rate limit bảo vệ **tốc độ** (chống spam/DoS, chia công bằng tài nguyên), cost guard bảo vệ **tiền**. Một client gọi đúng 10 req/phút nhưng mỗi request prompt dài và dùng model đắt vẫn đốt hết budget; ngược lại request rẻ nhưng dồn dập vẫn làm sập service. Hai lớp chặn hai rủi ro khác nhau.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health checks (`05-scaling-reliability/develop`)

```text
$ curl http://localhost:8000/health
{"status":"ok","uptime_seconds":3.2,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T07:46:42.169108+00:00","checks":{"memory":{"status":"ok","used_percent":11.3}}}
$ curl http://localhost:8000/ready
{"ready":true,"in_flight_requests":1}
```

- `/health` (**liveness**): process còn sống không. Fail → platform **restart** container. Không nên phụ thuộc Redis/DB, nếu không Redis chập chờn sẽ làm platform restart hàng loạt instance vô ích.
- `/ready` (**readiness**): instance có sẵn sàng nhận traffic không (đã load xong, Redis/DB kết nối được, chưa đang shutdown). Fail → load balancer **ngừng route** traffic tới instance đó nhưng không restart. Ở Part 6, `/ready` ping Redis và trả 503 khi mất Redis, còn `/health` vẫn 200.

### Exercise 5.2: Graceful shutdown

Gửi 1 request rồi `kill -TERM` ngay sau 50 ms:

```text
in-flight request -> HTTP 200
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
process exit code: 0
--- server log:
INFO:     Shutting down
INFO:     127.0.0.1:48618 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-26 14:46:45,297 INFO 🔄 Graceful shutdown initiated...
2026-11-26 14:46:45,297 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [4302]
```

**Kết quả:** request đang chạy vẫn hoàn thành (200), sau đó process thoát với exit code 0. uvicorn nhận SIGTERM → ngừng nhận kết nối mới → chờ request in-flight xong (tối đa `timeout_graceful_shutdown=30`) → chạy phần shutdown của `lifespan` → thoát.
Lưu ý: dòng `Received signal 15` của `handle_sigterm` in ra **cuối cùng** và handler này không làm gì thêm: uvicorn cài handler của nó lúc server start, nên handler đăng ký lúc import bị thay thế; uvicorn khôi phục lại handler cũ rồi gọi nó khi thoát. Vì vậy ở Part 6 mình dùng `lifespan` thay vì tự cài handler SIGTERM.

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` lưu session/history vào Redis (`session:<id>`, `SETEX` TTL 3600s) thay cho dict trong RAM. Lý do: khi scale ra N instance, mỗi instance có RAM riêng; request 1 của user vào instance A, request 2 vào instance B thì B không có history → bug. Đặt state ở Redis thì instance nào cũng đọc được, instance có thể bị kill/thay bất kỳ lúc nào mà không mất dữ liệu. Ở Part 6, rate limit (sorted set) và cost (INCRBYFLOAT) cũng nằm trong Redis; script chấm restart agent vẫn thấy 429.

### Exercise 5.4: Load balancing (`docker compose -p k5-day14-scaling up -d --build --scale agent=3`)

```text
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 16 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-2   Up 16 seconds (healthy)   8000/tcp
k5-day14-scaling-agent-3   Up 15 seconds (healthy)   8000/tcp
k5-day14-scaling-nginx-1   Up 15 seconds             0.0.0.0:8080->80/tcp
k5-day14-scaling-redis-1   Up 27 seconds (healthy)   6379/tcp

10 request POST http://localhost:8080/chat:
served_by= instance-7d8092 storage= redis
served_by= instance-e984d2 storage= redis
served_by= instance-c63000 storage= redis
served_by= instance-7d8092 storage= redis
served_by= instance-e984d2 storage= redis
served_by= instance-c63000 storage= redis
served_by= instance-7d8092 storage= redis
served_by= instance-e984d2 storage= redis
served_by= instance-c63000 storage= redis
served_by= instance-7d8092 storage= redis

$ docker compose -p k5-day14-scaling logs agent | grep "POST /chat"
agent-2  | ... "POST /chat HTTP/1.1" 200 OK   (x3)
agent-3  | ... "POST /chat HTTP/1.1" 200 OK   (x3)
agent-1  | ... "POST /chat HTTP/1.1" 200 OK   (x4)
```

Nginx chia **round-robin** đều cho 3 instance (4/3/3 request). `proxy_next_upstream error timeout http_503` giúp Nginx thử instance khác khi một instance lỗi.

### Exercise 5.5: Test stateless (`python test_stateless.py`)

```text
Session ID: f684417a-80ca-4b7c-a674-b13016b40989
Request 1: [instance-e984d2]  Q: What is Docker?
Request 2: [instance-c63000]  Q: Why do we need containers?
Request 3: [instance-7d8092]  Q: What is Kubernetes?
Request 4: [instance-e984d2]  Q: How does load balancing work?
Request 5: [instance-c63000]  Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-e984d2', 'instance-c63000', 'instance-7d8092'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
✅ Session history preserved across all instances via Redis!
```

**Giải thích:** 5 câu của cùng một session đi qua cả 3 instance, nhưng history đọc lại đủ 10 message (5 user + 5 assistant) vì mọi instance đọc/ghi cùng key `session:<id>` trong Redis.

Thêm một bước tự kiểm (script không tự tắt instance): tạo session, `docker stop k5-day14-scaling-agent-1`, rồi gọi tiếp 3 lượt cùng `session_id`:

```text
stop container /k5-day14-scaling-agent-1
200 served_by= instance-c63000
200 served_by= instance-e984d2
200 served_by= instance-c63000
history count = 8
```

Mất 1 instance, traffic tự chuyển sang 2 instance còn lại, cả 4 lượt (8 message) vẫn còn trong history.
