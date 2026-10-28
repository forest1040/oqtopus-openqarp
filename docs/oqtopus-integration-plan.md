# OpenQARP の OQTOPUS 対応

更新日: 2026-10-28。状態: OpenQARP Adapter、Qiskit V2 Sampler / Estimator、wheel 配布を実装。ローカル mock 試験と `qulacs` Cloud での検証を実施した。native Estimator は OQTOPUS の estimation job を使う。

## 方針と依存

- OpenQARP 回路・Qiskit 回路を OQTOPUS Cloud に送り、SamplingDistribution / Qiskit BitArray、期待値と標準偏差を受け取る。
- 接続先 `device_id` と SDK client は利用者が注入する。既定の接続先、認証情報、設定ファイルを書き換える処理を持たない。
- `oqtopus-client` の公開 API で `OqtopusJobSpec` を作り、`submit_job` / `wait_for_job` を使う。外部 client の設定を改変したり、自動 close したりしない。
- shots は job ごとに 1〜10,000。デバイスのゲート・容量・topology・配置・トランスパイルはサーバ側に任せ、クライアントでは取得・判定・補正しない。
- OQTOPUS counts は整数キーの bit `i` を古典ビット `i` として扱う。OpenQARP のタプルは qubit 0 が先頭、Qiskit の BitArray は SDK の bit 順を保つ。counts 境界で独自の bit reversal を行わない。
- Qiskit V2 primitives は `openqarp-oqtopus[qiskit]` の optional extra とする。Qiskit 未導入でも基本の OpenQARP API を import できる。
- 実装は `src/openqarp_oqtopus/` に置き、参照クローンには変更を加えない。`ref/openqarp/` と `ref/oqtopus-client/` は `.gitignore` で除外する。

### 調査した依存ソース

| 対象 | バージョン | 調査したコミット |
|---|---:|---|
| [OpenQARP](https://github.com/OpenQARP/openqarp) | 0.1.1 | `6c632b9a66383584a5944685abb81d33900c5836` |
| [oqtopus-client](https://github.com/oqtopus-team/oqtopus-client) | 1.3.0 | `d0b3d53c15025958197190b644efafdb06d202b1` |

開発用の uv path 依存はクローンの Git commit を固定しない。新しい開発環境では README の手順で上記 commit を用意し、`uv sync --locked --extra qiskit` を行う。更新時は commit、lock、検証結果を一緒に見直す。配布時はローカル path を依存メタデータへ含めず、OpenQARP は検証済み commit の配布物を使う。PyPI の openqarp 0.1.1 にはこの checkout で必要な公開 API と実行 hook の差があるため、バージョン番号だけで置き換えない。Qiskit 2.6.1 で検証した。

## ローカル環境と検証

検証環境は macOS 26.7.1 / Apple Silicon arm64、uv 0.13.5、CPython 3.12.15。開発用 OpenQARP は `ref/openqarp` から editable build した。C++20 compiler、CMake 3.20 以上を使い、CMake FetchContent が nanobind などを取得する。macOS deployment target は 13.3 以上。初回 build にはネットワークが必要で、メモリが限られる場合は `CMAKE_BUILD_PARALLEL_LEVEL=3` を設定する。

```sh
CMAKE_BUILD_PARALLEL_LEVEL=3 uv sync --locked --extra qiskit
MPLCONFIGDIR=artifacts/matplotlib uv run --locked --extra qiskit python -m pytest -q
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python -m pytest \
  -c ref/openqarp/pytest.ini \
  ref/openqarp/tests/test_algorithms/test_primitives/test_sampler.py \
  ref/openqarp/tests/test_devices/test_device.py \
  ref/openqarp/tests/test_postselection.py -q
```

通常の adapter tests は mock client を使い、認証や実通信を必要としない。2026-10-28 の最新一式は **67 passed**。旧 Estimator 実装段階では upstream の関連テスト **90 passed** を確認済み。最初の package 化時には wheel を隔離 uv 環境へ入れて **62 passed**、Qiskit extra なしでは **44 passed, 1 skipped** を記録した。これは native Estimator 変更前の履歴であり、今回の wheel へそのまま適用できる検証ではない。今回の native Estimator の wheel では隔離環境で67件成功、Qiskit extra なしの隔離環境で50件成功・1モジュールskipを確認した。

以前の CPU smoke (`scripts/local_smoke.py`) は OpenQARP simulator を Bell・X0/X1 の解析値と比較し、QASM3 と SDK sampling spec 生成を確認する。リモート job は送らない。実行結果は `artifacts/local-smoke.json`、QASM は `artifacts/bell.qasm`。OpenQARP のソース checkout / `qarpx` native extension は ABI と source-dir check を通過している。

## 接続環境

利用者が提供した `/Users/tmori/temp/oqt-cloud/a.py` の既存例は `qulacs` simulator へ Bell を送るもの。設定ファイル `~/.oqtopus` の `[default]` セクションを `OqtopusConfig.from_file("default", path="~/.oqtopus")` で読む。設定・token は repository に保存しない。

```sh
uv run --locked python scripts/check_oqtopus_connection.py \
  --config ~/.oqtopus --section default --device-id qulacs
```

以前の読み取り専用接続確認では `get_device("qulacs")` が simulator / available / 16 qubits を返した。通常の Adapter job では `get_device` を呼ばず、metadata 取得やゲート・容量判定を行わない。

## 公開 API と実行契約

通常 API は `OpenQARPOqtopusSampler` と `OpenQARPOqtopusEstimator`。Qiskit API は `openqarp_oqtopus.qiskit.OpenQARPOqtopusSamplerV2` と `OpenQARPOqtopusEstimatorV2`。

```python
from oqtopus_client import OqtopusClient, OqtopusConfig
from openqarp_oqtopus import OpenQARPOqtopusSampler, OpenQARPOqtopusEstimator
from qarp.blocks import SimpleBlock
from qarp.operators import QubitOperator

client = OqtopusClient(OqtopusConfig.from_file("default", path="~/.oqtopus"))
sampler = OpenQARPOqtopusSampler(device_id="qulacs", client=client, n_shots=1000)
bell = SimpleBlock(2)
bell.h(0)
bell.cx(0, 1)

distribution = sampler.sample(ket=bell)
value = OpenQARPOqtopusEstimator(sampler=sampler).estimate(
    ket=bell, operator=QubitOperator("Z0 Z1") + 0.5 * QubitOperator("X0 X1")
)
```

`device_id` は必須・read-only。Sampler は有限 shots の OpenQARP `SamplingDistribution` を返す。測定がなければ全 qubit の終端測定を追加し、counts を周辺化する。部分測定、bit mapping が identity でない測定、中間測定、reset、古典条件分岐は送信前に拒否する。元 block は変更しない。`sampler.executor` から OpenQARP primitives の `build` / `run` / `batch_run` を使える。

Estimator の回路は `qiskit.qasm3.dumps` または OpenQARP `QASM3Emitter` で形式変換する。Estimator ではローカル transpiler、gate decomposition、basis rotation、routing、measurement addition を行わない。QASM と estimation operator をそのままサーバへ渡し、device-side transpile は既定で Qiskit / optimization level 2。元回路の parameters は送信前に束縛する。ゲートサポート、topology、容量はサーバ責務。

### Native Estimator

OpenQARP と Qiskit の Estimator は同じ native estimation job 契約を使う。bound circuit と observable 全体の組ごとに **1 job** を作る。SDK へ `job_type="estimation"`、QASM3 program、`OqtopusEstimationOperator` 全項の配列を渡す。operator は qubit-indexed Pauli 文字列と係数を保つ。恒等項は `I 0` として送る。operator が空なら `I 0, coeff=0` を送る。

Estimator は OpenQARP `PauliAveraging` に委譲しない。Pauli term を分割・grouping しない。basis rotation、measurement append、Pauli 測定、term aggregation、ローカル standard deviation 計算を行わない。`grouping` は `None` のみ受理し、その他の指定は拒否する。成功結果は SDK の `exp_value` / `stds` を読む。core `estimate` は expectation value を返し、Qiskit V2 は `evs` / `stds` に server 値をそのまま格納する。missing/non-finite 値は不正結果として job ID 付きで通知する。

OpenQARP `estimate(..., n_shots=N)` は 1〜10,000 の指定 shots でそのまま1 jobを送る。Qiskit Estimator は `default_precision=None` のとき `1/sqrt(n_shots)` を既定 precision とする。PUB ごとの `precision` は `ceil(1 / precision**2)` shots/job へ写像し、10,000を超える場合は拒否する。precision は統計的目標で、server/device が達成を保証するものではない。Qiskit Pub metadata の `shots` は scalar。shot allocation や metadata の `shots_per_term` は使わない。

SDK wait で status が succeeded でない場合は `OqtopusJobError`、結果 payload 不正では `OqtopusResultError` を返す。timeout は remote cancel を意味しない。job ID は例外と履歴に残し、自動再送しない。

### Qiskit V2 Sampler

Sampler V2 は PUB binding / broadcasting を保ち、各 bound circuit を sampling job にする。Circuit にある classical register の各 BitArray は、integer counts を joint shot 列へ一度展開したうえで同じ列を register ごとに射影するため、レジスタ間の相関を維持する。サーバ counts は元の測定時間順を含まないので、その情報がないことを `shot_order` metadata に記録する。Circuit の cbit register mapping を QASM のまま送る。

## 実通信の検証結果

2026-10-28 に `qulacs` で native estimation と Qiskit V2 を確認した。

| 実行 | 結果 |
|---|---|
| Native Bell: `ZZ + 0.5 XX` | `1.5` |
| Native `|1+>`: `Z0 - 2 X1` | `-3` |
| Native identity observable | `2.5` |
| Qiskit Bell Sampler, 1,000 shots | `00=507`, `11=493` |
| Qiskit swapped classical-register Sampler | `low=0`, `high=1` |
| Qiskit Bell Estimator, `ZZ+0.5XX`, 400 shots | `evs=1.5`, `stds=0` |

Qiskit の3 job はすべて成功した。Native Qiskit 結果と job 情報は `artifacts/remote-native-qiskit.json` に保存。OpenQARP の既定3ケースも再実行して全件成功し、`artifacts/remote-native-estimation-basic.json` に保存した。Estimator の標準 Pauli operator や identity はサーバへ直接送られた。

### Server-side Estimator の既知問題

追加で実行した parameter cases は payload の width と束縛値が正しいことを確認した。client-side で補正せず、サーバーのエラーを job ID 付きで返す。

| 回路・observable | Server response | job ID |
|---|---|---|
| 2 qubits、X(q0)、`Z0 - 2 Z1` | `number of qubits(1) smaller than required index 1` | `06ae148b-b8fe-7904-8000-04d5cd7d08fe` |
| `Ry(0)`, `Z0` | `width 0`, index 0 の要求 | `06ae1493-e3ab-7b3f-8000-6b72659e3ddf` |
| `Ry(0.2)`, `Z0` | gRPC `INTERNAL: Failed to serialize response!` | `06ae149a-b1a4-7a91-8000-6ffa4db3f86a` |

`examples/oqtopus_estimation.py` の既定 native cases は成功済み。parameter failure は `--parameter-check` で再現できる。これらは client のローカル補正対象ではなく、OQTOPUS server の結果として扱う。

## 配布とテスト

Package は `src/` layout で wheel / sdist を作る。`ref/` クローン、認証設定、uv cache、artifacts を配布物に含めない。開発用 uv path overrides は維持するが、wheel METADATA に絶対ローカルパスは含めない。Qiskit は optional dependency。

```sh
uv run --locked --extra qiskit python -m pytest -q
uv build
```

Mock tests は OpenQARP / Qiskit の PUB shape、counts、LSB/classical mapping、native Estimator operator 配列、shots/precision、job failure、timeout、欠落または非有限な結果を確認する。認証付き Cloud examples は明示的に実行する場合に限り送信する。今回の native Estimator 変更後は開発環境で **67 passed**。最新 wheel をインストールした隔離 uv 環境で **67 passed in 0.96s**、Qiskit extra なしの隔離環境で **50 passed, 1 skipped**。開発ディレクトリ外から実行し、配布物のコードを検証した。

## 完了範囲

- [x] uv 開発環境・参照 commit・CPU native build を記録。
- [x] OpenQARP Sampler と共通 Cloud transport。
- [x] OpenQARP / Qiskit native Estimator、full-observable 1-job 契約。
- [x] Qiskit V2 Sampler / Estimator optional extra。
- [x] QASM/bit order、shots、status、result validation の mock coverage。
- [x] `qulacs` Cloud sampling / native estimation smoke cases。
- [x] Native implementation に対する isolated wheel verification。

Device gate compatibility、topology、capacity、placement、transpile は server-side の責務であり、client-side 追加実装の予定はない。
