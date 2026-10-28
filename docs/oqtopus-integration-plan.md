# OpenQARP の OQTOPUS 対応プラン

作成日: 2026-10-28。状態: 設計案。今回の範囲は uv 環境構築、ローカル動作検証、実装計画の保存。OQTOPUS 向けアダプタの実装・リモートジョブ送信は次段階。

## 方針と要求

- 公開 API は `OpenQARPOqtopusSampler(device_id=..., client=...)`。`device_id` は必須のキーワード引数としてインスタンス生成時に指定し、作成する全ジョブに反映する。固定の接続先をコードに埋め込まない。
- 今回の接続・サンプリング検証には利用者指定の `device_id="qulacs"` を使用する。他の device_id も指定できる API は維持する。
- 期待値計算には `OpenQARPOqtopusEstimator(sampler=...)` を用意し、Sampler と接続先・認証・送信処理を共有する。
- OQTOPUS との通信は `oqtopus-client` の公開 API を使う。
- Python は uv 管理。クローンを参照する開発環境の依存関係はルートの `pyproject.toml` と `uv.lock` で管理する。
- `ref/openqarp/` と `ref/oqtopus-client/` は親リポジトリの `.gitignore` で除外する。各クローン自身の `.git` は残す。
- 実装は親リポジトリの `src/openqarp_oqtopus/` で管理する。参照クローンに実装を置くと親の Git に記録されないため、参照用のソースには変更を加えない。

## 調査対象と再現性

ローカルのソースを調査した。最新のリモート HEAD への追従を前提にしない。

| 対象 | バージョン | 調査したコミット |
|---|---|---|
| [OpenQARP](https://github.com/OpenQARP/openqarp) | 0.1.1 | `6c632b9a66383584a5944685abb81d33900c5836` |
| [oqtopus-client](https://github.com/oqtopus-team/oqtopus-client) | 1.3.0 | `d0b3d53c15025958197190b644efafdb06d202b1` |

uv の path 依存は参照先の Git コミットを固定しない。新規環境では README の手順で上記コミットを用意し、`uv sync --locked` を実行する。更新時はコミット・lock・検証結果を一緒に見直す。

terra は Engine・QASM・SDK・結果変換の設計調査、luna はネイティブビルド・公開 API・検証対象の調査を担当。主担当がソースを確認し、環境構築・実行検証・本計画の統合を行った。

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

SDK の入力オブジェクト生成はオフラインの確認であり、サーバの QASM 受理・bitstring の意味まで検証するものではない。認証とリモート接続は後述のデバイス情報取得で確認した。リモートジョブ送信と実機動作は未検証。全 upstream テスト、化学・GPU・他 SDK 連携は今回の検証範囲外。

2026-10-28 の実行結果: 上記ビルド・スモークは exit 0。Bell の確率は `(0,0)=0.48875`、`(1,1)=0.51125`、期待値は `1.4999999999999998`。X(q0) / X(q1) はそれぞれ `(1,0)` / `(0,1)` に確率 1。OpenQASM 3.0 と SDK 入力生成も成功した。関連 upstream テストは **90 passed in 2.41s**、skip/failure なし。`qarp` は参照クローン、`qarpx` はこの `.venv` にビルドされたネイティブ拡張から読み込まれ、ABI/ソースディレクトリのチェックを通過した。

### 既存接続環境の確認

利用者提供の `/Users/tmori/temp/oqt-cloud/a.py` を読み取り確認した。quri-parts-oqtopus で Bell 回路を `qulacs` に 1,000 shots 送る例である。既存コードや環境は変更していない。

既存設定は `~/.oqtopus` の `[default]` セクションにあり、`url` / `api_token` を持つ。oqtopus-client の既定パスとは異なるため `OqtopusConfig.from_file("default", path="~/.oqtopus")` で明示的に読み込む。設定内容はリポジトリに保存しない。`conf.py` は設定ファイルを書き込むコードなので実行していない。

```sh
uv run --locked python scripts/check_oqtopus_connection.py --config ~/.oqtopus --section default --device-id qulacs
```

2026-10-28 に oqtopus-client 1.3.0 の `get_device("qulacs")` が成功した。返却情報は `device_type=simulator`、`status=available`、`n_qubits=16`、basis gates は `sx, x, rz, cx`。supported instructions は `measure` と前後空白を含む ` barrier` が返った。メタデータの文字列は検証時に扱いを確認する。これは当日の状態であり、実行時にも再取得する。ジョブ送信はしていない。

## 必要なインターフェイス

| インターフェイス | 責務 | 対応順 |
|---|---|---|
| `OpenQARPOqtopusSampler` | device_id 保持、有限 shots 測定、パラメータ束縛、`SamplingDistribution` 返却 | 最初 |
| `OpenQARPOqtopusEstimator` | OpenQARP `PauliAveraging` を使う期待値計算。Sampler と通信層を共有 | 次 |
| 内部 `_OqtopusEngine(Engine)` と `sampler.engine` | 既存 `Sampler` / `PauliAveraging` / 複合アルゴリズムとの互換、build/run/batch_run | Sampler と同時 |
| 実行履歴・ジョブ情報 | job_id、device_id、shots、状態、失敗回路の追跡 | Sampler と同時 |
| デバイス情報参照 | SDK の量子ビット数・対応命令などを検証に使用 | 必要な範囲で追加 |

独自の Device 階層は最初から設けない。OQTOPUS の `device_id` と OpenQARP の受動的な `Device` は異なる責務を持つ。接続先のノイズやトポロジーを、根拠なく OpenQARP の Device に変換しない。

API 案（未実装）:

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
sampler.engine.build([Sampler(ket=block, n_shots=1000)])
results = sampler.engine.run(params={theta: 0.2})
```

`sample` は既存の `Sampler`、`estimate` は既存の `PauliAveraging` を生成して同じ engine に委譲する。`measured_qubits` を sample に渡せるようにし、周辺化は OpenQARP の既存処理へ委譲する。device_id はインスタンスの読取専用設定とし、変更時は別インスタンスを作る。外部注入された client の設定を改変・自動 close しない。初期版の同一インスタンスでの並行呼出しは非対応とし、必要なら別インスタンスを使う。

## 実装設計

### OpenQARP と接続する位置

`ref/openqarp/qarp/engines/_engine.py` の既存 `Engine` を継承する。`_compile_one`、`_compile_stamp`、`_dispatch_one`、`_sweep` と検証 hook を実装し、テンプレートである build/run/batch_run を再実装しない。

- `_compile_one`: フラット命令の対応可否と幅を検査し、測定対応を確定する。初期版のローカル routing は行わない。
- `_compile_stamp`: クラス、device_id、QASM/分解・測定方針などコンパイルに影響する設定を含め、異なる接続先のコンパイル結果を流用しない。
- `_dispatch_one`: 名前によるパラメータ束縛 → QASM → SDK ジョブ → counts 変換 → `prim.run(results)`。複数回路でも順序を保つ。
- `_sweep`: パラメータ集合 × primitive × circuit の対応を保持。最初は順次実行し、SDK バッチ化は後から追加する。
- `provides_amplitudes` は明示的に False とする。基底クラスの既定値 True を継承すると、未対応の StateVector を誤って受理する。
- shots の優先順位は呼出し override > primitive > engine 既定。`sampler.sample` の override も同じ規則で実行層に渡す。
- `sampler.engine` では既存の `run_gradient` 等も表面に現れるため、初期版は gradient_methods を空にして、未対応要求を `CapabilityError` で拒否する。

Engine の hook や結果形式には private API が含まれるため、OpenQARP のコミット固定と互換テストが必要。クローンへの monkey patch は避ける。

### QASM と測定

既存 `qarp.emit.QASM3Emitter().emit(commands, n_qubits)` を使い、QASM を独自に手書き生成しない。SDK の `examples/run_sampling.py` にも OpenQASM 3 の例がある。

`Sampler.circuits()` は測定命令を追加しない。したがって、測定のない回路には q[i]→c[i] の全量子ビット終端測定を追加する。すでに同じ全量子ビット終端測定がある場合は二重追加しない。初期版は部分測定・非同番号の古典ビット対応・中間測定・Reset・古典条件分岐を明示拒否し、元の block は変更しない。

QASM3Emitter は ctrl/inv 修飾子や幅広いゲートを出力できるが、OQTOPUS の各デバイスが全構文を受理するとは限らない。対象で確認できた命令に分解し、数値束縛後の QASM を送信する。サーバ側 transpile の契約と設定を確認してから活用し、対応不明なゲートを黙って送らない。量子ビット数などの metadata を検証し、サーバから論理ビット順の結果が返るかを確認する。返らない場合は配置情報による逆変換を追加する。

### SDK と結果変換

`OqtopusJobSpec.sampling(device_id=self.device_id, program=qasm, shots=n_shots)` と `OqtopusClient.run_sampling(..., timeout=...)` を利用する。`counts_with_integer_keys()["counts"]` は整数キーを取得できるが、変換関数自体は文字列を二進数として読むのみで、サーバのビット順は保証しない。

OpenQARP 内部は整数の bit i が qubit i、外部表示は LSB 順タプル。OQTOPUS simulator で X(q0)、X(q1) を実行し、文字列の左右・古典レジスタ順・幅を確認する。確認後に必要な順列変換を境界だけに実装する。Bell 回路のみでは反転を検出できない。

OpenQARP の `distribution_from_result` / `outcome_arrays` に合わせ、`counts: dict[int, int]`、`n_shots`、`n_qubits` を持つ内部結果型を作る。`qx.SamplingResult` の直接生成は前提にしない。空結果・負数/非整数カウント・範囲外キー・不足 shots を検査し、正規化で不整合を隠さない。初期版は counts 合計が要求 shots と一致することを要求する。緩和後の準確率と divided_counts は別の契約が必要なので未対応とする。

同期 client の run_sampling は完了待ちをする。sample/estimate/run/batch_run は同期 API として明記する。タイムアウトは遠隔ジョブの取消しと同義ではない。SDK のステータス callback 等で job_id を保持できるかを確かめ、困難なら submit→wait→結果取得の公開 API を使う。送信後の例外で同じ回路を自動再送しない。失敗・取消・タイムアウトを区別し、元の SDK 例外と job_id を保持する。

### Estimator

最初は OpenQARP `PauliAveraging` のグルーピング・基底変換・測定回路・パリティ集計を再利用する。PauliAveraging がすでに追加した終端測定も検出する。恒等項だけの演算子は送信せず定数を返す。shots は原則として測定グループごとであり、総予算ではないことを公開 API で説明する。

SDK の `run_estimation` への直結は別段階とする。演算子・係数・qubit の対応、サーバ側測定設定、shots の意味が既存 Estimator と一致することを確認してから追加する。初期版の戻り値は既存 PauliAveraging と整合する実数。誤差・分散を返す拡張は後で設計する。

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
| 実機制約 | 対応ゲートと幅、transpile 後の論理ビット順が保持される | 任意の認証付き試験 |

mock 試験に加えて解析値との数値比較を持つ。別の自作変換による round-trip だけを正解としない。認証付き試験は明示した設定でのみ実行し、通常 CI は認証情報・ネットワークなしで通す。

## 段階とファイル配置

1. 環境・基準確認: 今回の uv、ignore、ローカル検証、計画保存。
2. Sampler: `src/openqarp_oqtopus/{__init__,sampler,_engine,_results}.py`、`tests/test_sampler.py`。`device_id` 必須の constructor、Engine 接続、QASM、counts、エラーとジョブ履歴を実装。mock での完了を先に確認する。
3. OQTOPUS simulator 検証: `examples/oqtopus_sampling.py`、任意実行の結合試験。X0/X1、Bell、パラメータ sweep を実行し、bit order と受理ゲートの契約を確定する。
4. Estimator: `src/openqarp_oqtopus/estimator.py`、`tests/test_estimator.py`、`examples/oqtopus_estimation.py`。PauliAveraging 連携を完成させ、期待値と複数測定グループを検証する。
5. 実機・発展: デバイス制約、配置の逆変換、必要に応じたバッチ最適化、SDK run_estimation、ジョブの再取得、対応可能な勾配。初期版の利用に必須にはしない。

実装時に `pyproject.toml` の src パッケージ設定と依存関係を追加する。OpenQARP に oqtopus-client を必須依存として追加しない。上流に取り込む場合は `AGENTS.md` と `docs/contracts/qarp_conventions.md` §14/15/17/18 に従い、SDK の遅延 import・optional export を含めて上流用計画を `docs/contributions/` に起こし、上流の Draft PR / green-light 手順に従う。今回の外部アダプタ計画に上流 PR の作成は含まない。

## 次段階で確認する運用情報

Sampler の実装と mock 試験はこの計画で進められる。接続先は `qulacs`、既存 config での認証とデバイス情報取得は確認済み。リモート実行時に server/transpiler の対応 QASM・ゲート、利用可能 shots 範囲と結果ビット順を検証する。実機対応へ進む際には追加で topology と配置の契約を確認する。
