# 🚀 ODYSSEY COPILOT — AI EVA Mission Operations Specialist
**NASA Space Apps Challenge 2026**: *Interplanetary Survival Guide: Martian Map*  
**Module**: `odyssey_copilot` (Plug-and-Play AI Assistant for Mars EVA Planning)

---

## 📌 Giới thiệu tổng quan (Overview)

`odyssey_copilot` là trợ lý ảo hỗ trợ phi hành gia (EVA Co-pilot / Capcom Radio Assistant) hoạt động tại **Jezero Crater, Sao Hỏa**. Được thiết kế như một module độc lập (**decoupled & plug-and-play**), copilot có thể được tích hợp vào bất kỳ giao diện web/map nào chỉ trong vòng **15 phút**.

### Điểm nổi bật:
* 🧠 **2-Pass Agentic Tool Loop**: Gọi LLM qua **OpenRouter** (mặc định `deepseek/deepseek-chat`, đổi bằng `OPENROUTER_MODEL`) với vòng lặp tự động gọi các công cụ địa hình & khoa học (tối đa 3 lượt).
* 🛡️ **Zero-Hallucination & Telemetry Guard**: Nghiêm ngặt từ chối bịa đặt các chỉ số sinh tồn của bộ đồ EVA (áp suất, lượng oxy bình dưỡng khí, nhịp tim), hướng dẫn phi hành gia kiểm tra bảng điều khiển analog phần cứng.
* 🛰️ **Grounding dữ liệu NASA**: Tích hợp toàn diện 25 mẫu đá lõi của xe tự hành Perseverance (bao gồm các điểm nóng sinh học cổ như *Cheyava Falls - Sample #25*) và địa hình HiRISE/MOLA.
* ⚡ **Offline Capcom Fallback**: Hệ thống vẫn phản hồi chính xác số liệu thực địa và điều khiển bản đồ ngay cả khi mất mạng hoặc chưa cấu hình API key.
* 🎨 **Giao diện HUD Mission Control**: Widget React nổi với phong cách trung tâm điều hành bay (Flight Director), tích hợp các chip câu hỏi nhanh (Quick Prompts) và bắn sự kiện Action 2 chiều ra bản đồ.

---

## 📂 Cấu trúc thư mục (Architecture)

```
odyssey_copilot/
├── backend/
│   ├── __init__.py          # Export sạch các thành phần router, schemas, tools, prompt
│   ├── prompt.py            # Capcom System Prompt + Bảng tra cứu 25 mẫu vật Jezero & địa danh
│   ├── schemas.py           # Pydantic v2 models (ChatRequest, ChatResponse, CopilotAction, LonLat)
│   ├── tools.py             # 3 công cụ: inspect_terrain, plan_route, get_sample_info (dual-mode)
│   ├── router.py            # FastAPI APIRouter (POST /api/copilot/chat) với 2-pass tool loop
│   └── test_copilot.py      # Bộ kiểm thử tự động độc lập (19 test cases)
├── frontend/
│   └── OdysseyCopilot.tsx   # React 19 + TypeScript + Tailwind widget dạng HUD floating dock
└── README.md                # Tài liệu hướng dẫn sử dụng & tích hợp này
```

---

## ⚙️ Cài đặt & Cấu hình (Configuration)

### 1. Cài đặt thư viện phụ thuộc
Module chỉ yêu cầu các thư viện tiêu chuẩn:
```bash
pip install fastapi uvicorn pydantic httpx
```

### 2. Cấu hình OpenRouter API Key
Tạo file `.env` tại thư mục gốc của project (`Challenge/AeroZone/.env`):
```env
OPENROUTER_API_KEY="sk-or-v1-..."
OPENROUTER_MODEL="deepseek/deepseek-chat"  # tùy chọn
```
*(Hoặc `export OPENROUTER_API_KEY="sk-or-v1-..."` trong terminal. Không có key thì copilot dùng offline fallback.)*

---

## 🧪 Hướng dẫn chạy thử độc lập (Test Drive)

Có 3 cách chạy thử nghiệm nhanh chóng:

### Cách 1: Chạy Interactive CLI trong Terminal (Khuyên dùng khi dev)
```bash
cd Challenge/AeroZone
python3 run_copilot_cli.py
```
**Các câu hỏi mẫu để test:**
* `Cho tôi thông tin chi tiết về mẫu đá Cheyava Falls số 25` $\rightarrow$ LLM gọi `get_sample_info`, trả về phân tích sinh học và sinh action `FOCUS_SAMPLE`.
* `Tìm đường an toàn nhất từ vị trí hạ cánh đến Cheyava Falls` $\rightarrow$ LLM gọi `plan_route`, tính toán và khuyến nghị tuyến đường né dốc > 30° và sinh action `PLAN_ROUTE`.
* `Kiểm tra độ dốc tại tọa độ 77.4509, 18.4446` $\rightarrow$ LLM gọi `inspect_terrain`.
* `Lượng oxy trong bình của tôi còn bao nhiêu?` $\rightarrow$ Kích hoạt cơ chế an toàn từ chối ảo giác.

### Cách 2: Chạy Standalone Server API (cổng 8001)
```bash
python3 run_copilot_server.py
```
Truy cập tài liệu tương tác Swagger UI tại: **[http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)** để test endpoint `POST /api/copilot/chat`.

### Cách 3: Chạy bộ kiểm thử tự động (Unit Test Suite)
```bash
python3 -m unittest -v odyssey_copilot/backend/test_copilot.py
```
*(Toàn bộ 19 bài test kiểm tra schema, chống ảo giác và tool execution đều pass 100%).*

---

## 🔌 Hướng dẫn Tích hợp vào Dự án Chính (15-Minute Team Integration)

### 1. Phía Backend (`backend/main.py` của team)
Chỉ cần đăng ký router copilot vào app FastAPI hiện tại:
```python
from odyssey_copilot.backend.router import router as copilot_router

# Đăng ký endpoint /api/copilot/chat
app.include_router(copilot_router, prefix="/api/copilot")
```

### 2. Phía Frontend (`frontend/src/App.tsx` của team)
Nhúng component `<OdysseyCopilot />` và bắt callback `onAction` để điều khiển bản đồ:

```tsx
import { OdysseyCopilot } from '../odyssey_copilot/frontend/OdysseyCopilot'

export default function App() {
  const handleCopilotAction = (action: any) => {
    console.log("Map Action từ Copilot:", action)
    
    switch (action.action_type) {
      case 'PLAN_ROUTE':
        // Cập nhật điểm đi/đến và vẽ tuyến đường trên bản đồ
        // action.payload: { start, end, routes, recommended, ... }
        break;
      case 'FOCUS_SAMPLE':
        // Phóng to camera tới tọa độ mẫu vật
        // action.payload: { lon, lat, name, sample_no, ... }
        map.flyTo([action.payload.lat, action.payload.lon], 16)
        break;
      case 'INSPECT_TERRAIN':
        // Hiển thị popup vi mô địa hình tại vị trí
        break;
    }
  }

  return (
    <div className="relative w-screen h-screen">
      <MarsMap ... />
      
      {/* Widget Copilot nổi ở góc màn hình */}
      <OdysseyCopilot onAction={handleCopilotAction} />
    </div>
  )
}
```

---

## 📜 Chuẩn dữ liệu Map Action JSON

Mỗi khi người dùng đưa ra câu lệnh liên quan đến bản đồ, backend trả về cấu trúc chuẩn:

```json
{
  "reply": "Capcom copy. Traverse route calculated to Cheyava Falls...",
  "action": {
    "action_type": "PLAN_ROUTE",
    "description": "Route planned to [77.3052, 18.4975]",
    "payload": {
      "start": {"lon": 77.4509, "lat": 18.4446},
      "end": {"lon": 77.3052, "lat": 18.4975},
      "recommended": "balanced",
      "routes": [...]
    }
  },
  "tools_called": ["plan_route"]
}
```

---
*Phát triển cho NASA Space Apps Challenge 2026 — Đội thi AeroZone.*
