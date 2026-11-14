# Odyssey Copilot — Pipeline Overview & Audit

Kiểm tra ngày 2026-11-14. Chạy thực tế qua `POST /api/copilot/chat` (cổng 8001), LLM đang dùng: OpenRouter `meta-llama/llama-3.3-70b-instruct`.

## 1. Pipeline

```
Client (OdysseyCopilot.tsx)
   │  POST /api/copilot/chat  {message, history?, current_location?{lon,lat}}
   ▼
router.chat()
   ├─ 1. Vitals guard (regex)  ── khớp → từ chối cố định, KHÔNG gọi LLM
   ├─ 2. Lấy vị trí: current_location (lon/lat hoặc x/y) → mặc định landing site 77.4509, 18.4446
   ├─ 3. OpenRouter (OPENROUTER_API_KEY, model mặc định meta-llama/llama-3.3-70b-instruct)
   │     Không có key hoặc lỗi → offline fallback (rule-based, keyword EN/VI)
   ├─ 4. Vòng tool tối đa 3 lượt: LLM + SYSTEM_PROMPT + 7 tool (tools luôn bật mỗi lượt),
   │     chạy mọi tool_call, kết quả gửi lại LLM để chuỗi gọi / tổng hợp
   ├─ 5. Tool trả source `local_fallback*` → câu trả lời được gắn cảnh báo "PLATFORM DATA OFFLINE"
   ├─ 6. Làm sạch token tool-call bị lộ; nếu LLM không gọi tool mà fallback nhận ra intent thì dùng fallback,
   │     ngược lại trả lời trực tiếp của LLM
   └─ 7. ChatResponse {reply, action?, tools_called}
```

### Các thành phần

| File | Vai trò |
|---|---|
| [backend/router.py](backend/router.py) | Endpoint, chọn provider, vòng lặp 2-pass, vitals guard, offline fallback |
| [backend/prompt.py](backend/prompt.py) | System prompt: bối cảnh Jezero, catalog 25 mẫu, hằng số MEDA/DONKI/Stack 2020, luật chống bịa vitals |
| [backend/tools.py](backend/tools.py) | 6 tool + `SAMPLES_DB` (25 mẫu) + `LANDMARKS_DB` |
| [backend/schemas.py](backend/schemas.py) | `ChatRequest`, `ChatResponse`, `CopilotAction` |
| frontend/OdysseyCopilot.tsx | Widget HUD, phát `onAction` ra bản đồ |

### 7 tool và nguồn dữ liệu

| Tool | Action phát ra | Nguồn dữ liệu |
|---|---|---|
| `get_sample_info` | FOCUS_SAMPLE | `SAMPLES_DB` nhúng trong code (25 mẫu) |
| `inspect_terrain` | INSPECT_TERRAIN | Platform `GET :8000/api/point` (DEM/slope/roughness thật); **timeout 2s → công thức ước lượng** |
| `plan_route` | PLAN_ROUTE | Platform `POST :8000/api/route`; **timeout 2s → planner giả lập (đường thẳng)** |
| `list_samples` | (không) | `SAMPLES_DB`, lọc theo rock class, trả số lượng |
| `get_weather_and_climate` | SHOW_WEATHER | `frontend/public/data/climate/meda_sols.json`, `dust_storm_stats.json` |
| `get_space_weather_and_radiation` | SHOW_SPACE_WEATHER | `space_weather/donki_mars.json`, `crustal_field.json` |
| `get_geology_context` | TOGGLE_LAYER | Geology GeoJSON (Stack 2020) + `data/metadata/geology_legend.json` |

## 2. Kết quả kiểm tra API

Endpoint hoạt động: 12/12 request trả 200, 2.7–11s/request.

| Câu hỏi | Kết quả | Đánh giá |
|---|---|---|
| Mẫu #25 Cheyava Falls (VI) | get_sample_info + FOCUS_SAMPLE | Tốt, khớp catalog |
| Sample #10 Swift Run | get_sample_info | Tốt, khớp catalog |
| Thời tiết Sol 168 | get_weather_and_climate | **Tốt — số khớp 100% với meda_sols.json** (219.64 K, 730.42 Pa, 1.251%, 3.19 m/s) |
| Rủi ro bức xạ | get_space_weather_and_radiation | Dùng dữ liệu DONKI thật (xem lưu ý) |
| Địa chất + trafficability | get_geology_context | Tốt (Máaz, risk 10) |
| Oxy còn bao nhiêu? | Vitals guard | Tốt, từ chối đúng, 2.7s, không gọi LLM |
| Có sự sống trên Mars? | Không tool, trả lời từ prompt | Tốt, đúng Sample #25 |
| World Cup 2022 | Từ chối ngoài phạm vi | Tốt |
| Terrain / Route | Có tool | **Phụ thuộc platform :8000 — xem vấn đề #1** |

## 3. Vấn đề phát hiện

**#1 (cao) Terrain/route lặng lẽ rơi về số giả khi platform :8000 tắt.**
Cùng tọa độ landing site 77.4509, 18.4446:

| | Platform thật | Fallback (server 8000 tắt) |
|---|---|---|
| elevation | -2485.95 m | -2559.94 m |
| slope | 2.08° | 4.47° |
| roughness | 0.37 m | 0.054 m |
| hazard | 36.37 | 16.7 |

Route fallback là một đường thẳng với `slope=4.5`, `hazard=12` hằng số và 3 profile hardcode (max slope 8.7°, "all below 30°"). Kết quả giả vẫn được LLM trình bày như số đo thật, kèm kết luận an toàn. Đây là rủi ro vì hệ thống nhấn mạnh "zero-hallucination". Payload fallback có trường `source: local_fallback_*` nhưng reply không nói gì cho người dùng.

**#2 (cao) LLM bịa chi tiết khi đã có tool result.**
Ở lần route với fallback, reply liệt kê waypoint "Séítah Formation Crossing / Delta Front Transition / Upper Fan Plateau" — tool không trả waypoint tên nào, đây là LLM tự suy diễn. Tương tự "polygonal fracture patterns" ở câu geology.

**#3 (trung bình) Lộ token tool-call thô của DeepSeek.**
Câu "Which samples are igneous and how many are there?" trả về JSON thô + `<｜tool▁calls▁begin｜>...` làm reply. Nguyên nhân: model muốn gọi tool lần 2 nhưng Pass 2 không truyền `tools`. Thêm nữa không có tool liệt kê/lọc mẫu và chỉ chạy tool đầu tiên → câu hỏi tổng hợp không trả lời được.

**#4 (trung bình) Ghi đè câu trả lời LLM bằng fallback; tọa độ ngoài AOI bị bỏ qua.**
Nếu LLM không gọi tool mà fallback khớp keyword thì dùng fallback. Hỏi "độ dốc tại 10.0, 50.0" → fallback bỏ tọa độ (ngoài vùng), trả số của landing site mà không báo. Fallback cũng khớp rộng ("từ", "đến", "gió", "sol") nên dễ nhận diện sai intent.

**#5 (thấp) Không xác định.**
- Reply luôn nói "Octavia E. Butler Landing" cho bất kỳ vị trí mặc định nào.
- Dữ liệu DONKI: reply nói "glancing blow arrival 2026-11-13" — đã là quá khứ so với ngày hiện tại 2026-11-14, nên không nên mô tả là "expected". Chưa kiểm chứng đối chiếu từng giá trị với donki_mars.json.
- `GEMINI_MODEL` trong `.env` không được đọc từ file (chỉ từ biến môi trường) nên chỉ có tác dụng khi export; hiện trùng mặc định nên không ảnh hưởng.
- README ghi Gemini 3.5 Flash Lite nhưng thực tế đang chạy DeepSeek qua OpenRouter.

## 4. Chưa kiểm tra được

- `test_copilot.py`: chạy xong **33 test, OK** (128s, gọi LLM thật; README ghi 19 là đã cũ). Lưu ý bộ test pass nhưng không bắt được các vấn đề #1–#4 ở trên.
- Nhánh Gemini trực tiếp chưa được chạy (OpenRouter luôn được ưu tiên).
- Chất lượng định lượng (số mẫu câu hỏi nhỏ, 12 câu).

## 5. Đã sửa (sau audit)

Bỏ hoàn toàn Gemini (chỉ OpenRouter). Vấn đề #1 (cảnh báo khi dùng số fallback), #2 (luật GROUNDING trong prompt), #3 (vòng tool 3 lượt, tools bật mọi lượt, lọc token rác, tool `list_samples`), #4 (tọa độ ngoài AOI báo rõ) đã xử lý; 33 test pass. Còn lại: vitals regex chưa bắt câu như "Lượng oxy còn bao nhiêu?" (không có từ "bình/my/suit").

### Đề xuất ban đầu (tham khảo)

1. Khi tool trả `source` bắt đầu bằng `local_fallback`, thêm cảnh báo vào reply ("platform offline — ước lượng, không dùng để định tuyến") hoặc trả lỗi thay vì số giả.
2. Thêm ràng buộc vào system prompt: chỉ dùng thông tin có trong tool result, không tự thêm waypoint/chi tiết.
3. Truyền `tools` trong Pass 2 hoặc loại bỏ nội dung chứa `tool▁call` khỏi reply; cho phép tool lặp ≤3 vòng; thêm tool `list_samples(filter)`.
4. Với tọa độ ngoài AOI, báo rõ thay vì thay bằng vị trí hiện tại.
