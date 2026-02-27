# Kế hoạch Triển khai: Binance Futures Scalping Bot với Machine Learning

Dự án này sẽ tạo ra một hệ thống Bot giao dịch tự động trên Binance Futures sử dụng chiến thuật Scalping, được tối ưu hoá bởi Machine Learning.

## Kiến trúc Hệ thống Đề xuất

Để đáp ứng tốt nhất yêu cầu về "Thống kê Học máy (ML)" và "Bảng điều khiển ReactJS", hệ thống sẽ được chia làm 2 phần (Cấu trúc Fullstack):

### 1. Backend & Machine Learning (Python)
Python là ngôn ngữ tiêu chuẩn và mạnh mẽ nhất cho Machine Learning (ML), xử lý dữ liệu tài chính (pandas) và giao tiếp ổn định với WebSocket.
- **Framework:** FastAPI (nhanh, hỗ trợ WebSocket tốt)
- **Kết nối Binance:** `ccxt` (lấy dữ liệu nến Klines) và `binance-futures-connector` (cho WebSocket thời gian thực).
- **Core ML:** Sinh tín hiệu (Signals) cho Scalping. Sử dụng `scikit-learn` hoặc `xgboost` để đánh giá xác suất thành công của các hệ thống chiến thuật như RSI, MACD, Bollinger Bands dựa trên dữ liệu giá quá khứ.
- **Tính năng:**
    - Lấy nến lịch sử (Historical Klines) để train mô hình.
    - Duy trì Websocket nhận nến thời gian thực.
    - Liên tục đưa ra dự đoán chiến thuật phù hợp nhất bằng mô hình ML.

### 2. Frontend Web Local (ReactJS)
- **Framework:** Vite + React + TypeScript + TailwindCSS.
- **Giao diện:** 
    - Biểu đồ nến thời gian thực dạng TradingView (dùng thư viện `lightweight-charts`).
    - Bảng thông số: Hiển thị các chiến thuật Scalping hiện tại và "Xác suất thành công" do AI/ML tính toán.
    - Báo cáo lịch sử lệnh/tín hiệu.
- **Giao tiếp:** Gọi REST API để lấy dữ liệu quá khứ và kết nối WebSocket tới Backend Python để cập nhật nến/signal theo Real-time.

---

## Giai đoạn Mở rộng: Tích hợp Machine Learning Thực tế (Real ML Backtest)

> [!IMPORTANT]
> Người dùng yêu cầu thay thế dữ liệu *ML Giả lập (Mock)* bằng một mô hình thực để dự đoán khả năng các lệnh sẽ thắng hoặc thua trong thực tế.

**Logic Huấn luyện Model:**
1. **Thu thập (Data Pipeline):** Tải về lịch sử 1000 nến `(OHLCV)` gần nhất của bộ danh sách 30+ Altcoin (PNUTUSDT, SOLUSDT, XRPUSDT v..v).
2. **Kỹ thuật Đặc trưng (Feature Engineering):** Tính toán các chỉ báo (RSI, EMA, MACD, Bollinger Bands) cho từng cây nến trong lịch sử đó.
3. **Gán nhãn (Labeling / Backtest Logic):** Vòng lặp giả lập lệnh Mua (Long) hoặc Bán (Short) tại các nến có điểm giao cắt. Nếu giá sau đó đi đúng hướng (vượt qua Take Profit trước Stop Loss), ta gán nhãn `Target = 1` (Win). Ngược lại gán nhãn `Target = 0` (Loss).
4. **Huấn luyện (Training):** Sử dụng `RandomForestClassifier` (đã khai báo trong thư viện `scikit-learn` Backend) cho máy học mối quan hệ giữa các chỉ báo và nhãn Win/Loss.
5. **Dự báo Điểm Vào Lệnh (Price Action Math):** Backend kết hợp với ATR (Average True Range) và Support/Resistance để tính toán giá Cắt Lỗ (Stop Loss) và Chốt Lời (Take Profit) với tỷ lệ Risk/Reward an toàn (ví dụ 1:1.5 hoặc 1:2).
6. **Dự đoán Trực tiếp (Live Predict):** Gọi mô hình đã huấn luyện (Model Inference) lên nến thời gian thực để trả về `% Win Probability` kèm theo chi tiết:
7. **Hệ thống Lệnh Mô phỏng (Auto Paper Trading):** Backend chạy thêm một Background Task liên tục lắng nghe giá cả và gọi hàm dự đoán ML. Nếu `% Win Probability` đạt ngưỡng cao (VD: > 65%), nó sẽ **Tự Động Mở Lệnh (Auto Open Order)**. Đồng thời, Background Task liên tục kiểm tra giá thanh lý (Hit TP/SL) để đóng lệnh chuyển sang Lịch sử (Close Orders). Bảng điều khiển Frontend (Web) mang tính chất Giám Sát (Chỉ Đọc), theo dõi quá trình Bot tự động cày tiền.
8. **Chiến Thuật MỚI - Đa Khung Thời Gian (H1 + M5 Pullback EMA):** Bổ sung thêm Logic bắt tín hiệu dựa trên sự đồng thuận của xu hướng lớn (H1) và điểm vào lệnh tối ưu ở xu hướng nhỏ (M5).
    - **Logic LONG:** 
        - Xu hướng H1: `Giá > EMA 8 > EMA 13 > EMA 21` (Uptrend).
        - Xu hướng M5: `Giá > EMA 8 > EMA 13 > EMA 21` (3 đường EMA tách nhau hướng lên).
        - Nến tín hiệu (M5): Giá thấp nhất (Low) chạm vào hoặc nhúng quá đường EMA 8, nhưng giá Đóng cửa (Close) phải nằm **trên** EMA 8.
    - **Logic SHORT:**
        - Xu hướng H1: `Giá < EMA 8 < EMA 13 < EMA 21` (Downtrend).
        - Xu hướng M5: `Giá < EMA 8 < EMA 13 < EMA 21` (3 đường EMA tách nhau hướng xuống).
        - Nến tín hiệu (M5): Giá cao nhất (High) chạm vào hoặc bật lên quá đường EMA 8, nhưng giá Đóng cửa (Close) phải nằm **dưới** EMA 8.

**Danh sách Altcoin hỗ trợ:**
"PNUTUSDT", "MANAUSDT", "KAITOUSDT", "NEARUSDT", "GMTUSDT", "TONUSDT", "GOATUSDT", "PONKEUSDT", "SAFEUSDT", "AVAUSDT", "TOKENUSDT", "MOCAUSDT", "PEOPLEUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "ENAUSDT", "MOVEUSDT", "ADAUSDT", "TURBOUSDT", "NEIROUSDT", "AVAXUSDT", "DOTUSDT", "XRPUSDT", "NEIROETHUSDT", "LINKUSDT", "XLMUSDT", "ZECUSDT", "ATOMUSDT", "BATUSDT", "NEOUSDT", "QTUMUSDT"

---

## Chi tiết Triển khai Mới

1. **Backend Python:**
   - Sửa `data_pipeline.py`: Bot phải thu thập thêm dữ liệu nến H1 (bên cạnh nến M5). Sau đó tính EMA 8, 13, 21 cho cả 2 khung thời gian. Áp dụng điều kiện Nến mồi (Signal Candle Pullback) để tạo nhãn.
   - Sửa đổi `ml_predictor.py` thành mô hình Load/Predict thực tế. Tích hợp chức năng Fetch H1 + M5 Realtime và kiểm tra điều kiện đồng bộ Đa Khung Thời Gian. Nếu không thỏa mãn (ví dụ H1 đang Down mà M5 đòi Long), có thể ép `win_prob = 0` hoặc đưa Features này cho AI tự lọc.
   - Thêm `order_manager.py`: Chứa class quản lý `open_orders` và `closed_orders`. Cải tiến: **Lưu trữ vĩnh viễn (Persistent Storage) vào file SQLite `trading_bot.db`**.
     - **Schema SQLite mới:** Sẽ bao gồm các trường quan trọng: `status` (PENDING, OPEN, CLOSED, CANCELED), `predicted_entry_price` (mức giá Entry do AI dự đoán) và `expiration_time` (hạn chót 20 phút để lệnh có hiệu lực).
   - Cung cấp 3 API endpoint: `GET /api/v1/orders/pending`, `GET /api/v1/orders/open` và `GET /api/v1/orders/closed`.
   
2. **Frontend ReactJS:**
   - Cập nhật Component `StrategyPanel.tsx` hiển thị 3 loại Giá (Entry/TP/SL) để giám sát phân tích.
   - Thêm Component mới `OrderPanel.tsx` (Bảng Quản lý Lệnh Tự Động) gồm 2 Tab:
        - **Open Orders:** Danh sách các lệnh mà AI **đã tự động vào** theo phân tích. Trạng thái Live báo lãi/lỗ tạm tính.
        - **Closed Orders:** Lịch sử các lệnh AI đã đóng khi chạm điểm TP / SL tự tính.

### Thư mục dự án: `binance-scalping-bot`

1. **Backend Python (`/backend`)**
   - File `main.py`: Chạy server FastAPI & WebSocket.
   - File `binance_client.py`: Quản lý API & WebSocket của Binance Futures.
   - File `strategies.py`: Định nghĩa các chiến thuật Scalping (RSI Crossover, MACD v.v).
   - File `ml_predictor.py`: Mô hình Machine Learning tính toán xác suất cho các chiến thuật.

2. **Frontend ReactJS (`/frontend`)**
   - Khởi tạo bằng lệnh `npm create vite@latest frontend -- --template react-ts`.
   - Các Component chính: `TradingChart.tsx` (Biểu đồ), `StrategyPanel.tsx` (Bảng tỷ lệ ML), `LiveTrades.tsx`.

## Kế hoạch Kiểm thử (Verification Plan)
### Kiểm thử tự động / Script
- Viết một kịch bản test trong Backend (`test_binance.py`) để kiểm tra:
  - Có thể ping Binance Futures API thành công không.
  - WebSocket có nhận stream nến (Klines) 1m ổn định không.
- Test hàm ML: Chạy model bằng file script độc lập xem model có trả ra tập xác suất dự đoán không bị lỗi hay không (`python backend/ml_predictor.py`).

### Kiểm thử Thủ công (Manual Verfication)
- Chạy ứng dụng Backend (FastAPI trên `localhost:8000`).
- Khởi động Frontend ReactJS (`npm run dev` trên `localhost:5173`).
- Mở trình duyệt web hiển thị dashboard:
  - Xác nhận biểu đồ nến di chuyển theo thời gian thực mỗi khi có tick giá mới từ Binance.
  - Kiểm tra bảng chiến thuật có tự động cập nhật phần trăm tỷ lệ % xác suất từ ML không.

## Giai đoạn Mở rộng 2: AI Dự đoán Lệnh Chờ (Pending Limit Orders)

> [!IMPORTANT]
> Thay vì vào ngay lệnh Market (Mở lệnh trực tiếp ở giá hiện tại), người dùng muốn hạn chế việc vào sai điểm (Bad Entry) bằng cách cho AI đoán ra một mức giá Entry tối ưu trong tương lai thông qua xác suất thống kê thống kê.

**Kiến trúc Cập nhật (Backend & ML):**
1. **Dự báo Entry Bù Giá (Statistical Entry Predictor):** ML không chỉ dự báo % Win, mà còn tính cả khoảng cách bù giá (VD: Giá hiện tại đang cao, AI đoán tỷ lệ rớt thêm 0.5% về một mức Support M5 râu nến là 80%).
2. **Khởi tạo Lệnh Limit (Pending Orders):** Khi AI chốt được một Setup, `order_manager` sẽ lưu nó vào Database với trạng thái `PENDING` thay vì `OPEN`.
3. **Quản lý Vòng Đời Lệnh (Background Task):**
    - **PENDING:** Lệnh đang chờ giá thị trường khớp với `predicted_entry_price` mà AI đề xuất. 
      - **Cơ chế Hết hạn:** Nếu sau **20 phút** (qua 4 cây nến M5) mà vẫn chưa khớp giá Entry ảo, lệnh sẽ tự động bị hủy (chuyển qua CANCELED).
    - **OPEN:** Lệnh PENDING đã được cắn giá. Hệ thống theo dõi y như cũ (chờ Hit TP / SL).
    - **CLOSED / CANCELED:** Lệnh OPEN đã chốt lời/cắt lỗ, hoặc Lệnh PENDING bị hủy do quá hạn 20 phút.

**Kiến trúc Cập nhật (Frontend - OrderPanel.tsx):**
- Giao diện (UI) sẽ chia làm 3 Tab/Cột riêng biệt:
    1. **Pending Orders:** Chờ khớp giá Entry.
    2. **Open Orders:** Hàng đã khớp, đang chạy PNL live.
    3. **Closed Orders:** Lịch sử ghi nhận TP/SL.

---

## Yêu cầu Phê duyệt từ Người dùng
> [!IMPORTANT]
> Cập nhật Kế hoạch mới nhất phản ánh tính năng chuyển đổi từ Market Orders sang Limit Orders (Pending). Bạn có đồng ý với logic PENDING -> OPEN -> CLOSED trên ứng dụng này không để tôi bắt tay vào code?
