# Backfill Compare: MVP một ZIP hoàn chỉnh cho mỗi borough

Chỉ mở rộng bằng ETL hiện có. Giữ nguyên dashboard, 48 Parquet lịch sử và dữ liệu đã công bố. Không tạo database mới. Chưa xác nhận dữ liệu đủ năm borough: phiên kiểm tra này không có `DATABASE_URL`, nên chưa truy vấn Supabase hoặc chạy backfill.

Theo trạng thái người dùng cung cấp và artifact Queens đã có, chỉ Queens / 11377 hoàn tất: 24.068 dòng nguồn, 24.064 yêu cầu hợp lệ, 2.768 khóa địa chỉ; 4 dòng thiếu địa chỉ hợp lệ bị loại khỏi Compare. Đây là số liệu artifact cũ, không phải kết quả kiểm tra PostgreSQL mới. `--plan-mvp` kiểm tra lại coverage và chỉ bỏ qua scope đã đối chiếu thành công trên PostgreSQL.

## API count đã chạy thực tế

Nguồn [NYC Open Data / erm2-nwe9](https://data.cityofnewyork.us/resource/erm2-nwe9.json), từ `2022-01-01T00:00:00` đến trước `2026-11-17T15:25:23`, giờ New York. Đếm toàn bộ bốn loại: `HEAT/HOT WATER`, `Noise - Residential`, `UNSANITARY CONDITION`, `PLUMBING`. Mỗi ZIP lọc đồng thời borough và ZIP, không lọc BBL và không lấy mẫu dòng.

| Borough | ZIP MVP | Số yêu cầu nguồn | Ước lượng byte đỉnh bổ sung |
| --- | --- | ---: | ---: |
| Queens | 11377 | 24.068 | 197.165.056 |
| Manhattan | 10044 | 1.621 | 13.279.232 |
| Brooklyn | 11231 | 9.002 | 73.744.384 |
| Bronx | 10464 | 901 | 7.380.992 |
| Staten Island | 10307 | 2.065 | 16.916.480 |
| Tổng | 5 ZIP | 37.657 | 308.486.144 |

Preflight đã chạy với API thật: đếm và đọc tối đa 100 dòng/ZIP để ước lượng; không chạy tải phân trang toàn bộ hoặc ghi PostgreSQL. Log cục bộ: `logs/compare-mvp-preflight.json` (không commit).

Nếu Queens được PostgreSQL xác minh hoàn tất, bốn ZIP còn lại là **13.589 dòng**, ước lượng **111.321.088 byte (~111,3 MB)** bổ sung. Cần cộng dung lượng database thực tế; chưa có số đo Supabase trong phiên này. Ước lượng bao gồm staging, dữ liệu chi tiết, index và dự phòng với tối thiểu 8.192 byte/dòng; không phải dung lượng đo sau khi nạp.

API count toàn năm borough cùng khoảng thời gian: Bronx 1.459.612; Brooklyn 1.097.184; Manhattan 807.430; Queens 651.634; Staten Island 83.648. Tổng **4.099.508**, tối thiểu khoảng **33,6 GB** theo cùng ước lượng. Không khởi động tải toàn NYC. MVP chọn địa bàn nhỏ để có lịch sử hoàn chỉnh trong từng ZIP; không đại diện toàn borough và không hỗ trợ mọi địa chỉ NYC. Các ZIP ngoài danh sách cần lập kế hoạch riêng trước khi nạp.

## Chạy trong PowerShell

Chạy từ `D:\nyc311_analysis`, dùng môi trường Python hiện có. Đặt biến môi trường `DATABASE_URL` bằng URI SSL của **Supabase hiện tại** (pooler nếu đang dùng); chương trình không đọc `.env` tự động. `SOCRATA_APP_TOKEN` tùy chọn. Tránh chạy thủ công đồng thời với một lượt Actions đang đồng bộ.

Đếm lại và kiểm tra dung lượng, chỉ đọc:

```powershell
.\.venv\Scripts\python.exe src/load_rental_clean.py --plan-mvp --budget-mb 400
```

Không có `DATABASE_URL`, lệnh này vẫn đếm API nhưng luôn báo `database_checked=false`, `can_backfill=false`. Có kết nối thì cộng dung lượng database hiện tại với toàn bộ ước lượng các ZIP chưa hoàn tất. Trần an toàn tối đa 400.000.000 byte; giới hạn dòng là hàng rào từ chối, không phải cắt dữ liệu. Nếu không đủ dung lượng, backfill dừng trước khi tải ZIP đầu tiên, không âm thầm giảm phạm vi.

**Một lệnh backfill / tiếp tục sau gián đoạn:**

```powershell
.\.venv\Scripts\python.exe src/load_rental_clean.py --backfill-mvp --budget-mb 400
```

Lệnh tự chạy lại preflight, rồi xử lý tuần tự Queens → Manhattan → Brooklyn → Bronx → Staten Island; bỏ qua scope đã xác minh hoàn tất. Trước từng ZIP, đo lại dung lượng và kiểm tra ước lượng phần còn lại. Khi một ZIP lỗi, dừng các ZIP sau; các ZIP đã hoàn tất được giữ nguyên.

Chạy lại đúng lệnh trên để tiếp tục: ETL hiện có lưu staging, cursor, run, scope và cutoff trong PostgreSQL, không phụ thuộc file checkpoint. Ctrl+C giữ checkpoint; tiến trình bị kill có thể phải đợi lease 15 phút hết hạn. Nếu generation nguồn đã đổi, ETL bỏ staging cũ và tải lại đầy đủ riêng ZIP đang dở, không ghép hai generation. Mỗi scope có cutoff bền vững riêng; ZIP hoàn tất được giữ nguyên cutoff cho đến lượt `--sync` tiếp theo.

ETL tải đủ mọi trang, lưu raw Parquet 13 trường và manifest/quality ở `data/rental/runs/<run_id>/`, kiểm tra count/generation/chất lượng rồi mới upsert địa chỉ hợp lệ theo `unique_key`. Các dòng không có địa chỉ hợp lệ vẫn ở raw Parquet và audit; không suy ra BBL hoặc tạo địa chỉ.

**Một lệnh kiểm tra kết quả trên Supabase:**

```powershell
.\.venv\Scripts\python.exe src/load_rental_clean.py --report-mvp
```

Lệnh chỉ đọc PostgreSQL, không gọi API. Chỉ thành công (exit code 0) khi **cả năm ZIP** đã có backfill, chất lượng đạt, count nguồn = hợp lệ + loại, count yêu cầu và địa chỉ thực tế khớp coverage. Xem `mvp.all_five_verified` và từng dòng `mvp.scopes`; `false` hoặc exit code khác 0 nghĩa là chưa nghiệm thu năm borough.

Báo cáo gồm số yêu cầu nguồn/đã công bố, số khóa địa chỉ, thiếu địa chỉ, ngày đầu/cuối, phạm vi và cutoff, thời điểm thành công gần nhất, `database_bytes`, `published_totals`, tổng số dòng kể cả legacy. `latest_runs` có số dòng trước từng lượt (`plan.database_rows_before`), inserted/updated/unchanged và dung lượng trước/sau. Preflight cũng in số dòng và dung lượng trước batch để đối chiếu.

Sau khi backfill và kiểm tra thành công, workflow hiện có `.github/workflows/rental-sync.yml` chạy `--sync`: chỉ lấy các scope đã backfill hoàn tất trong PostgreSQL, đối chiếu toàn lịch sử để thu cả yêu cầu mới lẫn yêu cầu cũ thay đổi. Không cần đổi dashboard hoặc kho Parquet toàn thành phố. Dữ liệu Compare chỉ được coi là hoàn tất tới cutoff được báo riêng cho từng ZIP.
