# ML Basic Và ML Candles BG

Tài liệu này mô tả ngắn gọn 2 nhánh mở lệnh chính trong hệ thống:

- `ML Basic`
- `ML Candles BG`

Trong code hiện tại:

- `ML Basic` dùng `entry_type = LIMIT`
- `ML Candles BG` dùng `entry_type = ML_CANDLES_BG`

## 1. ML Basic là gì

`ML Basic` là nhánh ML nền tảng, lấy signal từ scan chính rồi mở lệnh dạng limit khi giá chạm entry.

Đặc điểm chính:

- Dùng signal scan tổng quát trong vòng lặp paper engine
- Chỉ mở khi `raw_prob >= min_win_probability`
- Chỉ mở khi giá thị trường chạm vùng entry
- Trước khi mở còn đối chiếu thêm hướng của `predictor_candles` để tránh lệch side
- Lưu trade với `entry_type = LIMIT`

Flow thực tế:

1. Lấy signal scan
2. Kiểm tra `raw_prob`
3. Kiểm tra pause, re-entry cooldown, conflicting open trade
4. Kiểm tra BTC guard, hourly bad window, side cap, short streak guard
5. Điều chỉnh `required_min_win`
6. Chờ giá chạm entry
7. Chạy `basic pattern gate`
8. Chuẩn hóa TP/SL, leverage, risk
9. Tạo open trade

## 2. ML Basic đang bị chặn bởi gì

Các lớp chặn chính của `ML Basic`:

- `entry_block_reason`
- `basic_ml_post_dump_short_guard_reason`
- `basic_ml_post_pump_long_guard_reason`
- `basic_ml_btc_candle_confirmation_reason`
- `hourly_bad_window_guard`
- `bullish_short_nonfollow_min_win_bonus`
- `basic_ml_pattern_gate`
- `btc_filter`
- `open_side_cap`
- `instant_sl_guard`
- `reentry_cooldown`
- `conflicting_open_trade`
- `risk_pct_too_high`

Lưu ý:

- `ML Basic` vẫn cần giá chạm entry mới vào
- `ML Basic` hiện có `basic expectancy penalty`, tức là pattern xấu có thể làm giảm `effective probability`
- `post_pump_long` hiện tại đang để pass-through, chưa siết thêm min-win

## 3. ML Candles BG là gì

`ML Candles BG` là nhánh ML candles riêng cho universe BG, có cơ chế lọc cấu trúc mạnh hơn `ML Basic`.

Đặc điểm chính:

- Dùng predictor candles cho BG
- Lưu trade với `entry_type = ML_CANDLES_BG`
- Có `pattern gate` riêng cho BG
- Có `vol guard` riêng cho daily structure
- Có `countertrend block` riêng theo BTC regime
- Có block giờ riêng cho BG
- Có thể hiển thị thêm các trạng thái như `SURGE WATCH`, `VOL UP`, `ALLOW`, `STRICT`, `BLOCK`

Flow thực tế:

1. Lấy BG signal
2. Kiểm tra `raw_prob >= candles_bg_min_win_probability`
3. Kiểm tra block hour của BG
4. Kiểm tra BTC reversal block
5. Kiểm tra BG countertrend block
6. Tính `effective_prob`
7. Áp `hourly_bad_window_guard`
8. Áp `strict hour bonus`
9. Áp open cap, side cap, short streak guard, re-entry cooldown
10. Điều chỉnh entry theo BTC regime
11. Kiểm tra `entry_timing_reason`
12. Chạy `BG pattern gate`
13. Chạy `BG vol guard`
14. Chuẩn hóa TP/SL, leverage, risk
15. Tạo open trade

## 4. Vol Guard của ML Candles BG

`ML Candles BG` có daily volatility guard để chặn những coin BG có cấu trúc xấu hoặc quá nóng.

Các metric chính:

- `pump_ratio`
- `close_to_high_ratio`
- `atr_pct`
- `hot_day_count`
- `volume_top3_share`
- `volume_trend_ratio`
- `volume_acceleration_ratio`
- `volume_ramp`

Guard sẽ phân loại:

- `ALLOW`
- `STRICT`
- `BLOCK`

Ý nghĩa nhanh:

- `ALLOW`: cấu trúc ổn, có thể tiếp tục xét mở lệnh
- `STRICT`: cấu trúc nóng hoặc dữ liệu chưa đẹp, tùy config có thể siết thêm
- `BLOCK`: không được mở lệnh

Các badge UI liên quan:

- `SURGE WATCH`: coin đang nóng lên theo cấu trúc nhưng chưa thành `VOL UP`
- `VOL UP`: volume ramp đang mạnh
- `BLOCK`: guard đánh dấu không an toàn

## 5. Rule block hiện tại của BG

Backend hiện chặn `ML Candles BG` nếu:

- `assessment.level` đạt ngưỡng block theo config
- `ATR` vượt ngưỡng vol-guard
- `pump_ratio` vượt ngưỡng vol-guard
- `SHORT` gặp `VOL UP` khi config `short_block_on_vol_up = true`
- vol-guard fetch thất bại và không có cache usable

Điểm quan trọng:

- Hệ thống không còn chỉ nhìn mỗi `ATR` hay `VOL UP`
- Nếu guard đã đánh dấu `BLOCK`, backend sẽ block theo `assessment.level`
- Nếu không lấy được vol-guard từ market data, backend fail-safe thành `blocked_by_vol_guard: unavailable`

## 6. Khác nhau chính giữa ML Basic và ML Candles BG

`ML Basic`:

- Đơn giản hơn
- Dùng scan signal chính
- Vào lệnh khi entry bị chạm
- Từ bản hiện tại lưu `entry_price` theo giá fill market thực tế
- Có basic pattern gate
- Từ bản hiện tại đã dùng cùng daily vol-guard với BG để chặn coin đang `BLOCK`

`ML Candles BG`:

- Siết hơn
- Dùng BG universe và candles predictor riêng
- Có BG hour block riêng
- Có BG pattern gate riêng
- Có vol-guard daily structure riêng
- Có logic `SURGE WATCH`, `VOL UP`, `ALLOW/STRICT/BLOCK`

## 7. Cách đọc UI nhanh

Nếu bạn thấy một dòng `ML_CANDLES_BG`, nên đọc theo thứ tự này:

1. `Pattern`
2. `Win probability`
3. `Ready`
4. `Gate`
5. `Guard level`
6. `SURGE WATCH` hay `VOL UP`

Diễn giải nhanh:

- Có `VOL UP` chưa chắc luôn block `LONG`
- Có `VOL UP` sẽ block `SHORT` nếu config short-block đang bật
- Có `BLOCK` nghĩa là backend không nên cho mở lệnh
- Có `SURGE WATCH` là coin đáng theo dõi, không đồng nghĩa đã có entry hợp lệ

## 8. Mapping tên trong code

- `ML Basic` -> `LIMIT`
- `ML Candles BG` -> `ML_CANDLES_BG`
- `ML Test` -> `ML_TEST`

Nơi xem logic chính:

- `backend/app/services/paper_trading_engine.py`
- `backend/app/api/signals.py`
- `backend/app/api/paper_trades.py`

## 9. Ghi chú vận hành

- Không nên để nhiều backend cùng chạy trên cùng DB nếu cả hai đều có paper engine
- Nếu UI đọc process mới nhưng process cũ vẫn auto-open trade, bạn sẽ thấy cảm giác “đã sửa mà vẫn vào lệnh”
- Với `ML_CANDLES_BG`, vol-guard hiện là lớp chặn bắt buộc, kể cả khi market data bị lỗi tạm thời

## 10. Tóm tắt một câu

- `ML Basic` là nhánh limit-entry nền tảng
- `ML Candles BG` là nhánh BG được siết thêm pattern gate, BTC regime gate và vol-guard cấu trúc
