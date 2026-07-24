# Các API thực sự tham gia tính toán HE

Tài liệu này phân biệt:

- API thực hiện phép tính trên ciphertext;
- API thuộc vòng đời HE như setup, encrypt và decrypt;
- API chỉ chuẩn bị dữ liệu, lưu checkpoint hoặc tạo báo cáo.

## 1. Phép toán theo cột bằng HEIR/CKKS

| API | Chức năng |
|---|---|
| `OfficialCkksBinaryColumn` | Chương trình HEIR dùng cho CT+CT, CT−CT hoặc CT×CT |
| `OfficialCkksBinaryColumn.eval()` | Thực thi phép toán đã biên dịch trên ciphertext |
| `compile_checkpointable_binary_column()` | Biên dịch phép toán cột và bổ sung khả năng checkpoint |
| `EncryptedDataset.evaluate()` | Facade gọi phép toán HE trên hai ciphertext đã lưu |

Ví dụ tính `PAYMENT_DIFF`:

```python
payment_diff_ct = dataset.evaluate(
    "AMT_INSTALMENT",
    "AMT_PAYMENT",
)
```

Phép tính tương ứng trong OpenFHE:

```text
EvalSub(AMT_INSTALMENT.ct, AMT_PAYMENT.ct)
```

Kết quả `payment_diff_ct` vẫn là ciphertext.

## 2. SUM, MEAN và VARIANCE bằng HEIR/CKKS

| API | Chức năng |
|---|---|
| `OfficialCkksAggregate` | Aggregate một encrypted column |
| `compile_sum()` | Biên dịch SUM |
| `compile_mean()` | Biên dịch MEAN |
| `compile_variance()` | Biên dịch sample variance |
| `compile_checkpointable_sum()` | Biên dịch SUM có hỗ trợ checkpoint |
| `OfficialCkksBinaryColumnAggregate` | Thực hiện binary operation rồi aggregate |
| `compile_checkpointable_binary_column_aggregate()` | Biên dịch một branch SUM, MEAN hoặc VAR |
| `OfficialCkksBinaryColumnStatistics` | Thực hiện binary operation rồi trả SUM/MEAN/VAR |
| `compile_checkpointable_binary_column_statistics()` | Biên dịch combined statistics circuit |

Full `PAYMENT_DIFF` benchmark hiện chủ yếu dùng các branch riêng:

```python
branch = compile_checkpointable_binary_column_aggregate(
    operation="subtract",
    aggregate="sum",  # hoặc "mean", "variance"
    width=width,
    valid_count=valid_count,
    input_scale=input_scale,
)
```

Luồng tính toán:

```text
AMT_INSTALMENT.ct − AMT_PAYMENT.ct
                 │
                 ▼
           PAYMENT_DIFF.ct
                 │
                 ▼
          SUM / MEAN / VAR
```

Các branch riêng hiện đáng tin cậy hơn combined statistics circuit trên server
có tài nguyên hạn chế.

## 3. MIN/MAX bằng OpenFHE

| API | Chức năng |
|---|---|
| `OfficialOpenFheMinMax` | MIN/MAX bằng CKKS↔FHEW |
| `OfficialOpenFheMinMax.eval_min()` | Tính encrypted MIN |
| `OfficialOpenFheMinMax.eval_max()` | Tính encrypted MAX |
| `OfficialOpenFheColumnOps` | Arithmetic và MIN/MAX trong một OpenFHE context |
| `SourceBuiltOpenFheColumnMax` | MAX qua OpenFHE C++ được build trực tiếp trên server |

Server hiện tại dùng:

```python
SourceBuiltOpenFheColumnMax
```

Nguyên nhân là package Python `openfhe` không tương thích với bản OpenFHE
development đang được build và cài trên server. Vì vậy MAX được điều phối từ
Python nhưng thực thi trong source-built C++ runner.

## 4. API thuộc vòng đời HE

Những API này tham gia vòng đời HE nhưng không phải tất cả đều là phép tính
feature.

| API | Chức năng |
|---|---|
| `OfficialCkksBinaryColumn.setup()` | Tạo context và key material |
| `OfficialCkksBinaryColumn.encrypt()` | Mã hóa các cột đầu vào |
| `OfficialCkksBinaryColumn.eval()` | Thực hiện phép tính ciphertext |
| `OfficialCkksBinaryColumn.decrypt()` | Giải mã tại client/audit boundary |
| `OfficialCkksAggregate.encrypt()` | Mã hóa aggregate input |
| `OfficialCkksAggregate.eval()` | Thực hiện aggregate HE |
| `OfficialCkksAggregate.decrypt()` | Giải mã aggregate cuối |
| `EncryptedDataset.encrypt()` | Compile, setup, pack và mã hóa hai cột |
| `EncryptedDataset.evaluate()` | Thực hiện binary operation trên ciphertext |
| `EncryptedDataset.decrypt_result()` | Giải mã kết quả cuối tại client |

## 5. API không thực hiện phép tính HE

| API | Chức năng thực tế |
|---|---|
| `prepare_allowed_group_csv()` | Chọn, làm sạch và padding group tại client |
| `load_prepared_allowed_group()` | Đọc group đã chuẩn bị |
| `prepare_post_psi_groups()` | Chọn và sắp xếp group sau PSI |
| `EncryptedDataset.save()` | Serialize context, key và ciphertext |
| `EncryptedDataset.load()` | Deserialize context, key và ciphertext |
| `save_binary_column_aggregate_checkpoint()` | Lưu aggregate checkpoint |
| `load_binary_column_aggregate_checkpoint()` | Khôi phục aggregate checkpoint |
| `_run_multiple_allowed_groups()` | Điều phối nhiều benchmark process |
| `_multi_report()` | Tổng hợp kết quả và tạo report |

`EncryptedDataset.save()` và `EncryptedDataset.load()` thuộc hạ tầng lưu trữ
trạng thái mã hóa. Chúng không tạo ra phép tính feature mới.

## 6. Danh sách lõi tính toán HE

Các implementation chính thực sự thực hiện phép tính HE là:

```text
OfficialCkksBinaryColumn
OfficialCkksAggregate
OfficialCkksBinaryColumnAggregate
OfficialCkksBinaryColumnStatistics
OfficialOpenFheMinMax
OfficialOpenFheColumnOps
SourceBuiltOpenFheColumnMax
```

Quan hệ tổng quát:

```mermaid
flowchart LR
    PY[Python application]

    PY --> COL[OfficialCkksBinaryColumn]
    PY --> AGG[OfficialCkksAggregate APIs]

    COL --> HEIR[HEIR compiler]
    AGG --> HEIR
    HEIR --> GENERATED[Generated OpenFHE binding]
    GENERATED --> CKKS[OpenFHE CKKS runtime]

    PY --> MAX[SourceBuiltOpenFheColumnMax]
    MAX --> SWITCH[OpenFHE CKKS↔FHEW runtime]

    CKKS --> RESULT[Encrypted results]
    SWITCH --> RESULT
```

## 7. Trạng thái kết nối hiện tại

`EncryptedDataset` đã có thể:

```text
encrypt parent columns
→ save
→ load trong process mới
→ CT−CT
→ PAYMENT_DIFF.ct
→ final audit decrypt
```

Tuy nhiên, `PAYMENT_DIFF.ct` từ `EncryptedDataset.evaluate()` chưa được truyền
trực tiếp vào các API SUM/MEAN/VAR/MAX hiện tại. Full benchmark vẫn tạo các
branch riêng, mỗi branch nhận hai parent column rồi tự thực hiện subtraction
trước khi aggregate.

Adapter ciphertext chung giữa `EncryptedDataset` và các aggregate API là phần
kết nối còn thiếu nếu muốn toàn bộ pipeline dùng một ciphertext nguồn duy
nhất.

## 8. API ciphertext-in/ciphertext-out đơn giản

Hai backend cùng cung cấp API ciphertext-in/ciphertext-out nhỏ:

- `CkksSession`: OpenFHE Python binding, session nằm trong memory;
- `SourceBuiltCkksSession`: build runner C++ với OpenFHE đang cài tại
  `/usr/local/lib/OpenFHE`, context và ciphertext nằm trong checkpoint.

```python
from code.heir.python_api import CkksSession

he = CkksSession.create(
    width=4,
    input_scale=4096.0,
    ring_dimension=16384,
)

left_ct = he.encrypt_column(left)
right_ct = he.encrypt_column(right)

derived_ct = he.subtract(left_ct, right_ct)

sum_ct = he.sum(derived_ct)
mean_ct = he.mean(derived_ct)
variance_ct = he.variance(derived_ct)
minimum_ct = he.minimum(derived_ct)
maximum_ct = he.maximum(derived_ct)
```

API public:

```text
he.add(left_ct, right_ct)       -> EncryptedColumn
he.subtract(left_ct, right_ct)  -> EncryptedColumn
he.multiply(left_ct, right_ct)  -> EncryptedColumn

he.sum(column_ct)               -> EncryptedScalar
he.mean(column_ct)              -> EncryptedScalar
he.variance(column_ct)          -> EncryptedScalar
he.minimum(column_ct)           -> EncryptedScalar
he.maximum(column_ct)           -> EncryptedScalar
```

`EncryptedColumn` lưu reference đến session, public `valid_count` và scale.
Ciphertext từ hai session khác nhau bị từ chối trước khi gọi OpenFHE.

SUM, MEAN và VAR dùng public validity mask để loại padding. MIN/MAX dùng
duplicate padding nên padding không thay đổi extrema.

`CkksSession` sử dụng direct OpenFHE Python wrapper. Bản OpenFHE development
trên server hiện chưa có Python wrapper tương thích, vì vậy ví dụ real-data sử
dụng `SourceBuiltCkksSession`. Python vẫn là public orchestration API; các phép
tính thực tế chạy trong một runner C++ được link với OpenFHE trên server.

Checkpoint source-built chứa:

```text
encrypted_session/
├── manifest.json
├── public/
│   ├── context.bin
│   └── public.key
├── ciphertexts/
│   ├── AMT_INSTALMENT.ct
│   └── AMT_PAYMENT.ct
├── client_private/
│   └── audit_secret.key
└── runner/build/simple_ckks_session_runner
```

`--stage save` tạo context, mã hóa hai parent column và kết thúc process.
`--stage evaluate` mở process Python mới, kiểm tra hash context/ciphertext, load
hai parent ciphertext, sau đó mới chạy CT−CT và aggregate. Không có plaintext
parent nào được truyền vào evaluator. File prepared chỉ được client mở lại tại
final audit boundary để so sánh correctness.

Ví dụ đầy đủ:

```bash
python3 code/heir/examples/simple_ciphertext_api.py
```

Ví dụ real-data `PAYMENT_DIFF` từ đầu đến cuối, không có benchmark:

```bash
python3 code/heir/examples/payment_diff_simple_api_e2e.py \
  --stage roundtrip \
  --installments data/home_credit/installments_payments.csv \
  --allowed-sk-id-curr 100001 \
  --ring-dimension 16384 \
  --openfhe-dir /usr/local/lib/OpenFHE \
  --output-dir benchmark_runs/payment_diff_simple_api_100001 \
  --overwrite
```

Application code chỉ gọi:

```text
prepare_allowed_group_csv
→ SourceBuiltCkksSession.create
→ encrypt_column
→ process kết thúc
→ SourceBuiltCkksSession.load trong process mới
→ load_column cho hai parent ciphertext
→ subtract
→ sum / mean / variance / minimum / maximum
→ decrypt_scalar tại final audit boundary
```

Trong trial hiện tại, audit secret nằm trong `client_private/`. MIN/MAX process
cần secret này để tái tạo CKKS↔FHEW switching keys sau reload. Không chuyển thư
mục `client_private/` cho evaluator không đáng tin cậy; production deployment
cần tách client key service khỏi evaluator.
