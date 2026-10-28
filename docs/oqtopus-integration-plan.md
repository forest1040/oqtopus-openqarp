# OpenQARP の OQTOPUS 対応プラン

作成日: 2026-10-28。状態: OpenQARP / Qiskit の Sampler / Estimator 実装、wheel/sdist、qulacs でのクラウド検証を実施。最初に uv 環境構築・ローカル検証・設計を行い、利用者の続行指示後に実装とリモート検証を進めた。

## 方針と要求

- 公開 API は `OpenQARPOqtopusSampler(device_id=..., client=...)`。`device_id` は必須のキーワード引数としてインスタンス生成時に指定し、作成する全ジョブに反映する。固定の接続先をコードに埋め込まない。
- 今回の接続・サンプリング検証には利用者指定の `device_id="qulacs"` を使用する。他の device_id も指定できる API は維持する。
- 期待値計算には `OpenQARPOqtopusEstimator(sampler=...)` を用意し、Sampler と接続先・認証・送信処理を共有する。
- OQTOPUS との通信は `oqtopus-client` の公開 API を使う。
- shots は各ジョブ1〜10,000。対応命令・量子ビット容量・topology・配置・トランスパイルはサーバ側に任せ、こちらで検証・変換しない。
- OQTOPUS のビット順はリトルエンディアン。整数の bit i は古典ビット i、q[i]→c[i] では qubit i に対応する。追加のビット反転は行わない。
- Qiskit V2 の Sampler / Estimator インターフェイスを optional extra として提供する。
- Python は uv 管理。クローンを参照する開発環境の依存関係はルートの `pyproject.toml` と `uv.lock` で管理する。
- `ref/openqarp/` と `ref/oqtopus-client/` は親リポジトリの `.gitignore` で除外する。各クローン自身の `.git` は残す。
- 実装は親リポジトリの `src/openqarp_oqtopus/` で管理する。参照クローンに実装を置くと親の Git に記録されないため、参照用のソースには変更を加えない。

## 調査対象と再現性

ローカルのソースを調査した。最新のリモート HEAD への追従を前提にしない。

| 対象 | バージョン | 調査したコミット |
|---|---|---|
| [OpenQARP](https://github.com/OpenQARP/openqarp) | 0.1.1 | `6c632b9a66383584a5944685abb81d33900c5836` |
| [oqtopus-client](https://github.com/oqtopus-team/oqtopus-client) | 1.3.0 | `d0b3d53c15025958197190b644efafdb06d202b1` |

開発時の uv path 依存は参照先の Git コミットを固定しない。新規開発環境では README の手順で上記コミットを用意し、`uv sync --locked --extra qiskit` を実行する。更新時はコミット・lock・検証結果を一緒に見直す。配布メタデータでは OpenQARP を上記 Git コミットに固定する。公開 PyPI の openqarp 0.1.1 には SamplingDistribution export や実行 hook に差があるため、バージョン番号だけでの依存解決は使用しない。Qiskit の検証版は2.6.1、optional extra の対応範囲は2.3以上3未満。

terra は 実行層・QASM・SDK・結果変換の設計調査、luna はネイティブビルド・公開 API・検証対象の調査を担当。主担当がソースを確認し、環境構築・実行検証・本計画の統合を行った。

## ローカル検証

環境は macOS 26.7.1 / Apple Silicon arm64、uv 0.13.5、CPython 3.12.15。公開済み wheel への置き換えではなく、`ref/openqarp` を editable ビルドする。両プロジェクトは同じワークスペースの `.venv` にインストールし、別チェックアウトとの共用はしない。

```sh
CMAKE_BUILD_PARALLEL_LEVEL=3 uv sync --locked
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python scripts/local_smoke.py
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python -m pytest -c ref/openqarp/pytest.ini ref/openqarp/tests/test_algorithms/test_primitives/test_sampler.py ref/openqarp/tests/test_devices/test_device.py ref/openqarp/tests/test_postselection.py -q
```

ビルドには C++20 コンパイラ、CMake 3.20 以上が必要。scikit-build-core により CMake/Ninja を利用し、CMake FetchContent が nanobind v3.0.1、SymEngine v0.14.0 等を取得する。既定の SymEngine 有効・CPU 構成を維持する。Boost がなければ固定版のヘッダも取得される。macOS deployment target の下限は 13.3。初回はネットワークが必要。

`scripts/local_smoke.py` は以下を独立した解析値と比較する。実行結果は `artifacts/local-smoke.json`、QASM は `artifacts/bell.qasm` に出力する。

| 検証 | 期待値 |
|---|---|
| Bell 状態の 4,000 shots サンプリング | `(0,0)` と `(1,1)` のみ、各約 0.5、確率和 1 |
| Bell 状態の `ZZ + 0.5 XX` の厳密期待値 | 1.5 |
| q0 のみに X / q1 のみに X | LSB 順の `(1,0)` / `(0,1)` に確率 1 |
| 明示的な終端測定のある Bell 回路 | 同じ二つの状態のみ |
| OpenQASM 3 出力と SDK 入力生成 | 測定を含む文字列を `OqtopusJobSpec.sampling` に渡せる |

SDK の入力オブジェクト生成はオフラインの確認であり、サーバの QASM 受理・bitstring の意味は別途クラウド検証で確認した。認証とリモート接続は後述のデバイス情報取得で確認した。実機動作、全 upstream テスト、化学・GPU・他 SDK 連携は今回の検証範囲外。

2026-10-28 の実行結果: 上記ビルド・スモークは exit 0。Bell の確率は `(0,0)=0.48875`、`(1,1)=0.51125`、期待値は `1.4999999999999998`。X(q0) / X(q1) はそれぞれ `(1,0)` / `(0,1)` に確率 1。OpenQASM 3.0 と SDK 入力生成も成功した。関連 upstream テストは **90 passed in 2.41s**、skip/failure なし。`qarp` は参照クローン、`qarpx` はこの `.venv` にビルドされたネイティブ拡張から読み込まれ、ABI/ソースディレクトリのチェックを通過した。

### 既存接続環境の確認

利用者提供の `/Users/tmori/temp/oqt-cloud/a.py` を読み取り確認した。quri-parts-oqtopus で Bell 回路を `qulacs` に 1,000 shots 送る例である。既存コードや環境は変更していない。

既存設定は `~/.oqtopus` の `[default]` セクションにあり、`url` / `api_token` を持つ。oqtopus-client の既定パスとは異なるため `OqtopusConfig.from_file("default", path="~/.oqtopus")` で明示的に読み込む。設定内容はリポジトリに保存しない。`conf.py` は設定ファイルを書き込むコードなので実行していない。

```sh
uv run --locked python scripts/check_oqtopus_connection.py --config ~/.oqtopus --section default --device-id qulacs
```

2026-10-28 に oqtopus-client 1.3.0 の `get_device("qulacs")` が成功した。返却情報は `device_type=simulator`、`status=available`、`n_qubits=16`、basis gates は `sx, x, rz, cx`。supported instructions は `measure` と前後空白を含む ` barrier` が返った。この情報は当日の接続確認記録。通常のジョブ実行ではメタデータ取得やデバイス制約のローカル判定を行わない。このデバイス情報取得ではジョブを送信していない。

### アダプタのクラウド検証結果

デバイス情報取得後、実装したアダプタから `qulacs` に以下の8ジョブを送信した。すべて1,000 shots、`succeeded`。認証設定は既存ファイルを参照し、リポジトリにコピーしていない。

| 回路・計算 | 解析値 | 結果 |
|---|---|---|
| X(q0) / X(q1) | `(1,0)` / `(0,1)` に確率1 | 一致。追加のビット反転なし |
| Bell | `(0,0)` / `(1,1)` に確率約0.5 | 0.521 / 0.479 |
| Bell の ZZ + 0.5 XX | 1.5 | 1.5 |
| X(q0) 状態の Z0 − 2 Z1 | −3 | −3.0 |
| Ry(0), Ry(π/3), Ry(π) の P(1) | 0, 0.25, 1 | 0, 0.255, 1 |
| 恒等項だけの期待値 | 2.5、追加送信0回 | 一致 |

実行コマンドは README の `examples/oqtopus_sampling.py` / `examples/oqtopus_estimation.py` を参照。ジョブIDと結果は Git 管理外の `artifacts/remote-sampling.json` / `artifacts/remote-estimation.json` に保存した。

アダプタのオフライン試験は **37 passed**。解析値に加え、shots・パラメータ・測定配置・結果不整合・能力検査・失敗時のジョブID保持を確認した。パッケージ化後の関連 upstream 試験も **90 passed**。terra が Estimator 試験と実装レビュー、luna が Sampler 試験を担当した。

## 必要なインターフェイス

| インターフェイス | 責務 | 対応順 |
|---|---|---|
| `OpenQARPOqtopusSampler` | device_id 保持、有限 shots 測定、パラメータ束縛、`SamplingDistribution` 返却 | 最初 |
| `OpenQARPOqtopusEstimator` | OpenQARP `PauliAveraging` を使う期待値計算。Sampler と通信層を共有 | 次 |
| `OpenQARPOqtopusSamplerV2` / `OpenQARPOqtopusEstimatorV2` | Qiskit の PUB 入力と PrimitiveResult、BitArray、evs/stds | 実装済み |
| 内部 `_OqtopusCloudExecutor` と `sampler.executor` | 既存 `Sampler` / `PauliAveraging` / 複合アルゴリズムとの互換、build/run/batch_run | Sampler と同時 |
| 実行履歴・ジョブ情報 | job_id、device_id、shots、状態、失敗回路の追跡 | Sampler と同時 |
| 共通 `_CloudTransport` | SDK 入力の生成、shots 検証、送信・待機・履歴・counts | 実装済み |

独自の Device 階層は最初から設けない。OQTOPUS の `device_id` と OpenQARP の受動的な `Device` は異なる責務を持つ。接続先のノイズやトポロジーを、根拠なく OpenQARP の Device に変換しない。

公開 API:

```python
from oqtopus_client import OqtopusClient, OqtopusConfig
from openqarp_oqtopus import OpenQARPOqtopusSampler, OpenQARPOqtopusEstimator

client = OqtopusClient(config)  # config: 利用者が用意した OqtopusConfig
sampler = OpenQARPOqtopusSampler(
    device_id="qulacs",  # 今回使用するシミュレータ
    client=client,
    n_shots=1000,
    timeout=300.0,
)
distribution = sampler.sample(ket=block, params={theta: 0.2}, n_shots=2000)
estimator = OpenQARPOqtopusEstimator(sampler=sampler)
value = estimator.estimate(ket=block, operator=hamiltonian, params={theta: 0.2})

# OpenQARP 標準プリミティブとも同じ実行層を利用する
from qarp.algorithms import Sampler
sampler.executor.build([Sampler(ket=block, n_shots=1000)])
results = sampler.executor.run(params={theta: 0.2})
```

`sample` は既存の `Sampler`、`estimate` は既存の `PauliAveraging` を生成して同じ実行層に委譲する。`measured_qubits` を sample に渡せるようにし、周辺化は OpenQARP の既存処理へ委譲する。device_id はインスタンスの読取専用設定とし、変更時は別インスタンスを作る。外部注入された client の設定を改変・自動 close しない。初期版の同一インスタンスでの並行呼出しは非対応とし、必要なら別インスタンスを使う。

## 実装設計

### OpenQARP と接続する位置

`ref/openqarp/qarp/engines/_engine.py` の既存の実行基底クラスを継承する。独自クラス名は `_OqtopusCloudExecutor` とし、公開プロパティは `sampler.executor` とする。oqtopus-client を使ってクラウド API に接続する。`_compile_one`、`_compile_stamp`、`_dispatch_one`、`_sweep` と検証 hook を実装し、テンプレートである build/run/batch_run を再実装しない。

- `_compile_one`: フラット命令列の測定対応を確定する。デバイス向けの変換・routing はサーバ側の責務。
- `_compile_stamp`: クラス、device_id、QASM・測定方針を含め、異なる接続先の準備済み回路を流用しない。
- `_dispatch_one`: 名前によるパラメータ束縛 → QASM → SDK ジョブ → counts 変換 → `prim.run(results)`。複数回路でも順序を保つ。
- `_sweep`: パラメータ集合 × primitive × circuit の対応を保持。最初は順次実行し、SDK バッチ化は後から追加する。
- `provides_amplitudes` は明示的に False とする。基底クラスの既定値 True を継承すると、未対応の StateVector を誤って受理する。
- shots の優先順位は呼出し override > primitive > executor 既定。`sampler.sample` の override も同じ規則で実行層に渡す。
- `sampler.executor` では既存の `run_gradient` 等も表面に現れるため、初期版は gradient_methods を空にして、未対応要求を `CapabilityError` で拒否する。

実行層の hook や結果形式には private API が含まれるため、OpenQARP のコミット固定と互換テストが必要。クローンへの monkey patch は避ける。

### QASM と測定

既存 `qarp.emit.QASM3Emitter().emit(commands, n_qubits)` を使い、QASM を独自に手書き生成しない。SDK の `examples/run_sampling.py` にも OpenQASM 3 の例がある。

`Sampler.circuits()` は測定命令を追加しない。したがって、測定のない回路には q[i]→c[i] の全量子ビット終端測定を追加する。すでに同じ全量子ビット終端測定がある場合は二重追加しない。初期版は部分測定・非同番号の古典ビット対応・中間測定・Reset・古典条件分岐を明示拒否し、元の block は変更しない。

命令列はパラメータ束縛後、そのまま QASM3Emitter に渡す。ローカルの GateSet/Transpiler、ゲート分解、GPhase 除去、対応命令・容量・topology・配置の判定は設けない。Qiskit 入力では `qiskit.qasm3.dumps` を使う。どちらも表現形式の変換で、対象デバイス向けのトランスパイルはサーバに任せる。

### SDK と結果変換

`OqtopusJobSpec.sampling(device_id=self.device_id, program=qasm, shots=n_shots)` を生成し、`submit_job` / `wait_for_job` を利用する。`counts_with_integer_keys()["counts"]` で整数キーを取得する。

ビット順は利用者の指定どおりリトルエンディアンで統一する。OpenQARP の出力タプルは qubit 0 が先頭、Qiskit の文字列は古典ビット0が右端。counts の整数キーをそのまま使う。X(q0) / X(q1) と Qiskit の古典ビット写像を入れ替えた回路で整合性を確認した。

OpenQARP の `distribution_from_result` / `outcome_arrays` に合わせ、`counts: dict[int, int]`、`n_shots`、`n_qubits` を持つ内部結果型を作る。`qx.SamplingResult` の直接生成は前提にしない。空結果・負数/非整数カウント・範囲外キー・不足 shots を検査し、正規化で不整合を隠さない。初期版は counts 合計が要求 shots と一致することを要求する。緩和後の準確率と divided_counts は別の契約が必要なので未対応とする。

実装では job_id を確実に保持できる `submit_job` → `wait_for_job` の公開 API を使用する。sample/estimate/run/batch_run は同期 API。SDK の wait は失敗・取消状態でも結果を返すため、status が succeeded の場合だけ counts を読む。タイムアウトは遠隔ジョブの取消しと同義ではない。失敗・取消・タイムアウトを区別し、元の SDK 例外は cause として保持する。job_id は例外と `sampler.jobs` に残す。SDK 内部の登録・アップロード中に例外が起きた場合は job_id を得られない可能性がある。自動再送はしない。

サーバ側変換は既存 a.py と同じ `transpiler_lib=qiskit`、`optimization_level=2` を既定とした。ローカルのトランスパイルは完全に除去した。QASM の手書き生成はしない。緩和は既定で無効。shots は利用者指定の1〜10,000を適用する。SDK schema が許す値でもこの範囲外は送信前に拒否し、恒等項だけの計算にも同じ制限を適用する。

`batch_run` は全パラメータ集合を送信前に検証する。単一 primitive の複数測定回路も一括準備する。一方、`executor.run` で複数 primitive を直接扱う場合は primitive 単位で準備・送信するため、後続の検証や実行が失敗しても先行ジョブは残る。バッチ実行を含め、複数ジョブの取消しやトランザクションは提供しない。

### Estimator

最初は OpenQARP `PauliAveraging` のグルーピング・基底変換・測定回路・パリティ集計を再利用する。PauliAveraging がすでに追加した終端測定も検出する。恒等項だけの演算子は送信せず定数を返す。shots は原則として測定グループごとであり、総予算ではないことを公開 API で説明する。

SDK の `run_estimation` への直結は別段階とする。演算子・係数・qubit の対応、サーバ側測定設定、shots の意味が既存 Estimator と一致することを確認してから追加する。初期版の戻り値は既存 PauliAveraging と整合する実数。誤差・分散を返す拡張は後で設計する。

### Qiskit V2 と配布

`openqarp_oqtopus.qiskit` の2クラスは、それぞれ Qiskit の BaseSamplerV2 / BaseEstimatorV2 を実装する。`SamplerPub.coerce` / `EstimatorPub.coerce` で入力を正規化し、BindingsArray による束縛と NumPy broadcasting を使用する。全 PUB を準備してからジョブを返し、同一インスタンス内では順次実行する。[Qiskit primitives API](https://quantum.cloud.ibm.com/docs/en/api/qiskit/2.3/primitives)

Sampler はレジスタごとの BitArray を返す。集計 counts を joint outcome 列に展開し、その同じ列から各レジスタを取り出す。元の shot 時間順は得られないことを metadata に記録する。Qiskit の非同番号古典ビット写像と複数レジスタは QASM をそのまま送って処理する。

Estimator は Pauli 項を独立に測定し、必要な基底測定回路を追加する。各項の shots は係数二乗和と precision から決め、10,000を超える要求は拒否する。evs と counts 由来の stds を返す。恒等項は送信なし。有限 shots の統計的な精度目標であり、デバイスノイズを含む精度の保証はしない。

`uv build` で wheel/sdist を生成する。`MANIFEST.in` で ref・認証設定・キャッシュ・生成物を除外する。基本 import は Qiskit を要求せず、`[qiskit]` extra で追加できる。別 uv 環境では配布 wheel と検証済み OpenQARP Git コミットをインストールして検証する。

Qiskit から qulacs への4ジョブが成功した。Bell は `00=510, 11=490`、測定写像を入れ替えた回路は `low=0, high=1`、`ZZ+0.5XX` は `evs=1.5, stds=0`。結果と job_id は `artifacts/remote-qiskit.json` に保存した。

最終テスト結果: 開発環境で **62 passed**。wheel を別の uv 環境にインストールし、開発ディレクトリの外から実行して **62 passed in 3.22s**。Qiskit extra なしの別 uv 環境では **44 passed, 1 skipped**。OpenQARP は指定 Git コミットからビルドされたものを使用し、モジュールの参照先が環境内の site-packages であることを確認した。wheel/sdist に ref・認証設定・キャッシュ・実行結果が含まれないこと、配布メタデータに絶対ローカルパスが含まれないことも確認した。

## テスト計画と完了条件

| 対象 | 独立した判定基準 | 実施先 |
|---|---|---|
| Sampler、device_id | 注入 mock が指定された ID・QASM・shots を受け取る。異なる ID の二つのインスタンスで混線しない | ローカル CI |
| counts / endian | 人手で定めた X0=`(1,0)`、X1=`(0,1)` と周辺分布 | mock + 認証付き simulator |
| 測定追加 | 無測定→全測定追加、正規の終端測定→重複なし、非対応測定→送信前拒否 | ローカル CI |
| パラメータ | Ry(θ) の P(1)=sin²(θ/2)、未束縛時は送信なし | ローカル CI + simulator |
| sweep / 複数回路 | 異なる既知結果を割り当て、集合・primitive・circuit の順序と shots 優先順位を検査 | ローカル CI |
| Estimator | Bell の ZZ+0.5XX=1.5、X0 状態の Z0=-1/Z1=+1、恒等項は送信 0 回 | ローカル CI + simulator |
| ジョブ失敗 | SDK 例外注入、timeout/取消、job_id 保持、重複送信なし | ローカル CI |
| 能力・入力検証 | amplitudes/EXACT/initial_state/MCM/不正 counts を受理しない。build 後の変更も run 時再検査 | ローカル CI |
| サーバ側責務 | 命令列を分解せず渡し、ローカルでデバイス容量や配置を判定しない | ローカル CI |
| shots |1〜10,000の受理と範囲外の送信前拒否 | ローカル CI |

mock 試験に加えて解析値との数値比較を持つ。別の自作変換による round-trip だけを正解としない。認証付き試験は明示した設定でのみ実行し、通常 CI は認証情報・ネットワークなしで通す。

## 段階とファイル配置

- [x] 環境・基準確認: uv、ignore、ローカル検証、計画保存。
- [x] Sampler: `src/openqarp_oqtopus/{__init__,sampler,_executor,_results}.py`、`tests/test_sampler.py`。`device_id` 必須の constructor、実行層、QASM、counts、エラーとジョブ履歴を実装。
- [x] OQTOPUS simulator 検証: `examples/oqtopus_sampling.py`。X0/X1 と Bell で bit order と受理ゲートを確認。
- [x] Estimator: `src/openqarp_oqtopus/estimator.py`、`tests/test_estimator.py`、`examples/oqtopus_estimation.py`。PauliAveraging と接続し、解析的な期待値・測定基底変換・パラメータ sweep を確認。
- [x] Qiskit V2、optional extra、wheel/sdist の生成、サーバ側トランスパイルへの一本化、shots 上限10,000を実装。
- [ ] 発展: 必要に応じたバッチ最適化、SDK run_estimation、対応可能な勾配。topology・配置・デバイス向け変換はサーバ側に任せる。HPC での直接接続は将来の別方式として扱う。

`pyproject.toml` に src パッケージ設定を追加し、`openqarp-oqtopus` として editable install した。OpenQARP に oqtopus-client を必須依存として追加していない。上流に取り込む場合は `AGENTS.md` と `docs/contracts/qarp_conventions.md` §14/15/17/18 に従い、SDK の遅延 import・optional export を含めて上流用計画を `docs/contributions/` に起こし、上流の Draft PR / green-light 手順に従う。今回の外部アダプタ計画に上流 PR の作成は含まない。

## 次段階で確認する運用情報

`qulacs` の認証、基本 QASM、サーバ側変換、リトルエンディアン、OpenQARP/Qiskit の Sampler/Estimator 実行は確認済み。shots は1〜10,000。他デバイスでも対応命令・topology・配置・トランスパイルのクライアント側実装は追加しない。
