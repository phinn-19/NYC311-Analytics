# NYC 311 Service Request Analytics

## 1. Giới thiệu

Project phân tích đặc điểm và xu hướng yêu cầu dịch vụ công tại New York trong giai đoạn **2022–2025** dựa trên dữ liệu **NYC 311 Service Requests**.

Mục tiêu chính:

- phân tích xu hướng số lượng yêu cầu theo thời gian;
- xác định các loại complaint phổ biến;
- phân tích thời điểm phát sinh yêu cầu theo tháng, ngày trong tuần và giờ;
- phân tích phân bố theo Borough và Community Board;
- phân tích thời gian đóng yêu cầu;
- phân tích cơ quan tiếp nhận yêu cầu;
- phân tích các kênh gửi yêu cầu;
- xây dựng dashboard Streamlit có bộ lọc tương tác và cơ chế cập nhật dữ liệu định kỳ.

> Phạm vi phân tích được cố định theo `created_date` từ `2022-01-01` đến trước `2026-01-01`.  
> Cơ chế refresh chỉ cập nhật trạng thái/thông tin của các request thuộc giai đoạn 2022–2025, không thêm request mới được tạo trong năm 2026.

---

## 2. Nguồn dữ liệu

Nguồn chính:

- **NYC Open Data**
- Dataset: **311 Service Requests from 2020 to Present**
- Dataset ID: `erm2-nwe9`

Snapshot ban đầu của project gồm:

| Năm | Số request |
|---:|---:|
| 2022 | 3,169,960 |
| 2023 | 3,224,723 |
| 2024 | 3,456,770 |
| 2025 | 3,655,041 |
| **Tổng** | **13,506,494** |

Dữ liệu được lưu theo tháng dưới dạng **Parquet + Snappy** để giảm dung lượng và tăng tốc độ đọc.

---

## 3. Cấu trúc project

```text
nyc311_analysis/
│
├── .streamlit/
│   └── config.toml
│
├── dashboard/
│   ├── app.py
│   ├── build_dashboard_cache.py
│   ├── refresh_data.py
│   └── run_refresh.ps1
│
├── data/
│   ├── external/
│   │   └── nyc_community_districts.geojson
│   ├── metadata/
│   │   └── refresh_state.json
│   ├── processed/
│   │   └── *_processed.parquet
│   └── raw/
│       └── *_raw.parquet
│
├── logs/
│   └── refresh_*.log
│
├── notebooks/
│   ├── 01_data_collection.ipynb
│   ├── 02_data_cleaning.ipynb
│   └── 03_eda_visualization.ipynb
│
├── outputs/
│   ├── charts/
│   └── tables/
│
├── src/
│
├── .gitignore
├── README.md
└── requirements.txt
```

---

## 4. Môi trường

### Python

Khuyến nghị dùng Python 3.11+.

### Tạo môi trường ảo

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### Cài thư viện

```powershell
pip install -r requirements.txt
```

`requirements.txt`:

```text
pandas==3.0.6
numpy==2.5.3
requests==2.34.2
matplotlib==3.11.2
seaborn==0.13.2
plotly==7.1.0
streamlit==1.65.0
pyarrow==25.0.1
jupyter==1.1.1
```

> Không cần đóng gói thư mục `.venv/` khi nộp project. Người dùng khác chỉ cần tạo môi trường mới và chạy `pip install -r requirements.txt`.

---

## 5. Quy trình xử lý dữ liệu

### Bước 1 — Thu thập dữ liệu

Notebook:

```text
notebooks/01_data_collection.ipynb
```

Notebook thực hiện:

- kết nối NYC Open Data;
- kiểm tra metadata và kết nối API;
- đếm số request theo tháng;
- tải dữ liệu 2022–2025 theo 48 monthly partitions;
- kiểm tra `row_count`, `unique_key`, phạm vi ngày và schema;
- lưu dữ liệu raw dạng Parquet;
- tải Community District GeoJSON phục vụ bản đồ.

Trong notebook, biến:

```python
RUN_FULL_DOWNLOAD = False
```

được giữ `False` trong bản nộp để tránh tải lại toàn bộ ~13.5 triệu dòng khi vô tình Run All.

---

### Bước 2 — Làm sạch và tạo biến mới

Notebook:

```text
notebooks/02_data_cleaning.ipynb
```

Các bước chính:

- chuẩn hóa missing/placeholder;
- chuẩn hóa kiểu dữ liệu;
- chuẩn hóa borough, status và channel;
- parse `created_date`, `closed_date`;
- kiểm tra duplicate;
- tạo các biến:
  - `year`
  - `month`
  - `weekday`
  - `hour`
  - `is_weekend`
  - `closure_valid`
  - `closure_hours`
  - `has_coordinates`

Quy tắc `closure_valid`:

```text
closed_date tồn tại
AND closed_date >= created_date
AND closed_date <= source snapshot
```

`closure_hours` chỉ được tính với các record có closure hợp lệ.

---

### Bước 3 — EDA và trực quan hóa

Notebook:

```text
notebooks/03_eda_visualization.ipynb
```

Các nhóm phân tích:

- tổng quan số lượng request;
- complaint type;
- xu hướng theo năm/tháng;
- seasonality;
- weekday × hour;
- borough và Community Board;
- chuẩn hóa borough theo dân số Census 2020;
- closure-time distribution;
- agency;
- request channel.

Các bảng tổng hợp phục vụ dashboard được lưu trong:

```text
outputs/tables/
```

---

## 6. Chạy dashboard

Từ thư mục root project:

```powershell
streamlit run dashboard/app.py
```

Dashboard gồm 6 trang:

1. `Overview`
2. `Temporal`
3. `Geography`
4. `Agencies`
5. `Closure`
6. `Channels`

Global filters:

- Year
- Borough
- Complaint Type

Dashboard sử dụng các bảng cache đã được tổng hợp để tăng tốc độ tải và tương tác.

---

## 7. Cơ chế cập nhật dữ liệu định kỳ

### 7.1. Mục đích

Population nghiên cứu luôn cố định là các request được tạo trong **2022–2025**.

Tuy nhiên, request lịch sử vẫn có thể được NYC cập nhật về:

- `status`;
- `closed_date`;
- thông tin xử lý;
- thời điểm cập nhật xử lý.

Vì vậy project có cơ chế đọc lại nguồn và cập nhật định kỳ thay vì giữ snapshot hoàn toàn tĩnh.

### 7.2. Script cập nhật

```text
dashboard/refresh_data.py
```

Luồng xử lý:

```text
NYC Open Data
      ↓
kiểm tra các record 2022–2025 được cập nhật sau lần đồng bộ trước
      ↓
fetch incremental rows
      ↓
upsert theo unique_key
      ↓
rebuild raw partition bị ảnh hưởng
      ↓
rebuild processed partition bị ảnh hưởng
      ↓
rebuild dashboard cache
      ↓
update refresh_state.json
```

Incremental query sử dụng `resolution_action_updated_date` và có lookback ngắn để giảm nguy cơ bỏ sót các cập nhật được công bố trễ.

### 7.3. Khởi tạo metadata refresh

```powershell
python dashboard/refresh_data.py --bootstrap
```

### 7.4. Kiểm tra nguồn nhưng chưa sửa dữ liệu

```powershell
python dashboard/refresh_data.py --check-only
```

Kiểm tra chính xác từ watermark, không dùng lookback:

```powershell
python dashboard/refresh_data.py --check-only --no-lookback
```

### 7.5. Refresh thật

```powershell
python dashboard/refresh_data.py
```

Nếu có thay đổi, script sẽ:

- tải các record thay đổi;
- cập nhật monthly raw Parquet;
- xử lý lại các monthly processed partitions liên quan;
- build lại dashboard cache;
- cập nhật `data/metadata/refresh_state.json`.

---

## 8. Build lại dashboard cache thủ công

Nếu cần rebuild cache mà không gọi API:

```powershell
python dashboard/build_dashboard_cache.py
```

Các cache chính:

```text
dashboard_request_cube.parquet
dashboard_time_cube.parquet
dashboard_board_cube.parquet
dashboard_agency_cube.parquet
closure_overall_summary_final.parquet
dashboard_cache_metadata.json
```

---

## 9. Refresh định kỳ bằng Windows Task Scheduler

Wrapper:

```text
dashboard/run_refresh.ps1
```

Có thể cấu hình Task Scheduler chạy mỗi tuần.

Ví dụ:

```text
Program/script:
powershell.exe
```

Arguments:

```text
-ExecutionPolicy Bypass -File "D:\nyc311_analysis\dashboard\run_refresh.ps1"
```

Start in:

```text
D:\nyc311_analysis
```

Mỗi lần chạy, log được lưu trong:

```text
logs/
```

Log ghi lại:

- số request thay đổi;
- các monthly partitions bị ảnh hưởng;
- incremental watermark mới;
- source snapshot mới;
- trạng thái refresh.

---

## 10. Dataset Snapshot trong dashboard

Sidebar dashboard hiển thị:

- Date range;
- Total requests;
- Source snapshot;
- Refresh status;
- Refresh details.

Các giá trị được đọc từ:

```text
data/metadata/refresh_state.json
```

nên dashboard phản ánh lần cập nhật nguồn gần nhất.

---

## 11. Một số lưu ý khi diễn giải

### Request count

Số request không tương đương số người dân duy nhất.

### Service demand

Dữ liệu phản ánh **recorded 311 service requests**, không phải toàn bộ nhu cầu dịch vụ công thực tế.

### Closure time

```text
closure_hours = closed_date - created_date
```

được diễn giải là **recorded closure time**, không phải thời gian xử lý vật lý thực tế của agency.

Phân phối closure time lệch phải mạnh, vì vậy ưu tiên median và percentile hơn mean.

### Agency

Số request theo agency phản ánh workload/service portfolio, không dùng trực tiếp để xếp hạng hiệu suất vì mỗi agency xử lý các loại yêu cầu khác nhau.

### Population normalization

Chỉ số request theo dân số sử dụng population Census 2020 làm denominator tham chiếu, không có nghĩa một người dân gửi đúng số request tương ứng.

---

## 12. Đóng gói project

Không đưa các thư mục/file sau vào ZIP nộp bài:

```text
.venv/
.git/
__pycache__/
.ipynb_checkpoints/
*.pyc
```

Nên giữ:

```text
dashboard/
data/
notebooks/
outputs/
logs/
src/
.streamlit/
requirements.txt
README.md
.gitignore
```

---

## 13. Cách chạy nhanh project

```powershell
# 1. Activate environment
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies nếu cần
pip install -r requirements.txt

# 3. Chạy dashboard
streamlit run dashboard/app.py
```

Refresh dữ liệu thủ công:

```powershell
python dashboard/refresh_data.py
```

Build cache thủ công:

```powershell
python dashboard/build_dashboard_cache.py
```

---

## 14. Công nghệ sử dụng

- Python
- Pandas
- NumPy
- Requests
- PyArrow / Parquet
- Matplotlib
- Seaborn
- Plotly
- Streamlit
- NYC Open Data / Socrata API
- Windows Task Scheduler
