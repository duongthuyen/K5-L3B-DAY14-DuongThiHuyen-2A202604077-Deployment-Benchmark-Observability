# Deployment Information

## Public URL

**https://day14-agent-scke.onrender.com**

Service deploy là agent Part 6 (`06-lab-complete/`), đúng như CP6 của lớp K5.

> Gói free của Render tự "ngủ" sau ~15 phút không có request. Vì vậy request đầu tiên có thể mất 30–60 giây để service khởi động lại; các request sau phản hồi bình thường.

## Platform

**Render**, dùng Blueprint [`06-lab-complete/render.yaml`](06-lab-complete/render.yaml):

| Service | Loại | Ghi chú |
|---|---|---|
| `day14-agent` | Web service, runtime Docker | Build từ `06-lab-complete/Dockerfile` (multi-stage, user non-root), region Singapore, plan free, `healthCheckPath: /health` |
| `day14-redis` | Key Value (Redis) | Lưu state của rate limit và cost guard; agent nhận `REDIS_URL` qua `fromService.property: connectionString` |

Railway được thử trước nhưng bị nền tảng chặn ở bước upload: *"We limited this workspace after detecting unusual activity. Upgrade to a paid plan…"*. Chi tiết trong `MISSION_ANSWERS.md`, Part 3. File [`06-lab-complete/railway.toml`](06-lab-complete/railway.toml) vẫn được giữ và vẫn hợp lệ.

## Test Commands

Đặt biến trước: `BASE=https://day14-agent-scke.onrender.com`. Biến `AGENT_API_KEY` lấy đúng giá trị đã đặt trên Render; giá trị này không ghi trong repo.

### 4 lệnh tự kiểm (CP6)

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả thật, chạy ngày 2026-11-26:

```text
200
200
401
200
```

### Health check

```bash
curl https://day14-agent-scke.onrender.com/health
# {"status":"ok","environment":"production"}
curl https://day14-agent-scke.onrender.com/ready
# {"status":"ready"}      ← Redis (Key Value) kết nối được
```

### API test (có xác thực)

```bash
curl -X POST https://day14-agent-scke.onrender.com/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "question": "What is deployment?"}'
# {"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":4,"output_tokens":19},"cost_usd":1.2e-05}
```

Gọi không có key:

```text
{"error":"Invalid or missing API key","status":401}
```

### Rate limit trên cloud (`RATE_LIMIT_PER_MINUTE=10`)

Gửi 12 request liên tiếp:

```text
200 200 200 200 200 200 200 200 429 429 429 429
HTTP/2 429
retry-after: 54
```

Chỉ 8 request đầu nhận 200 vì key này đã dùng 2 lượt trong cùng cửa sổ 60 giây (2 lệnh `/ask` có key ở phía trên). State của rate limit nằm trong Redis.

### Script chấm với public URL

```text
$ python check_production_ready.py --static-only --public-url https://day14-agent-scke.onrender.com
✅ 12. Public URL ready (Railway / Render config)
     ✅ Public URL: GET https://day14-agent-scke.onrender.com/health → 200
```

## Environment Variables Set

Các biến này đặt trên nền tảng (Render dashboard / Blueprint), không ghi trong code:

| Biến | Giá trị / nguồn |
|---|---|
| `PORT` | Render tự cấp; `CMD` trong Dockerfile đọc `${PORT:-8000}` |
| `AGENT_API_KEY` | Secret, khai báo `sync: false` → nhập tay trên dashboard (chuỗi ngẫu nhiên 48 ký tự hex) |
| `REDIS_URL` | `fromService` → connection string của Key Value `day14-redis` |
| `ENVIRONMENT` | `production` |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | Không đặt → dùng mặc định `10.0` trong `config.py` |
| `LOG_LEVEL` | Không đặt → mặc định `INFO` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png): Render dashboard, service `day14-agent` đang Live
- [Service running](screenshots/running.png): `GET /health`, `/ready`, `/` trên URL public đều trả 200
- [Test results](screenshots/test.png): 4 lệnh CP6 (200 / 200 / 401 / 200), rate limit 429 kèm `Retry-After`, kết quả `--public-url`
