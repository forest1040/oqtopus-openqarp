# OpenQARP / OQTOPUS Cloud アダプタ

OpenQARP / Qiskit の回路を oqtopus-client で OQTOPUS Cloud に送信し、測定分布・期待値を取得する Python ライブラリです。トランスパイル、対応命令、topology、配置はサーバ側に任せます。shots は1〜10,000、ビット順はリトルエンディアンです。設計・検証記録は [docs/oqtopus-integration-plan.md](docs/oqtopus-integration-plan.md) にあります。

## ライブラリとしてインストール

Python 3.12 と uv を使用します。配布物を生成します。

```sh
uv build
```

`dist/openqarp_oqtopus-0.1.0-py3-none-any.whl` とソース配布物が生成されます。別の uv プロジェクトから wheel を追加できます。

```sh
uv add "/path/to/dist/openqarp_oqtopus-0.1.0-py3-none-any.whl[qiskit]"
```

Qiskit は optional extra です。OpenQARP だけを使う場合は `[qiskit]` を省略します。OpenQARP は使用している API を持つ検証済み Git コミットに固定しています。同じ 0.1.1 名の PyPI wheel には API 差があるためです。初回は依存の取得と OpenQARP のネイティブビルドが必要です。wheel/sdist には `ref`・認証設定・キャッシュ・実行結果を含めません。PyPI への公開は行っていません。

## Qiskit から利用

[Qiskit V2 primitives](https://quantum.cloud.ibm.com/docs/en/api/qiskit/2.3/primitives) の `BaseSamplerV2` / `BaseEstimatorV2` に対応しています。以下はクラウドにジョブを送信します。

```python
from oqtopus_client import OqtopusClient, OqtopusConfig
from qiskit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp
from openqarp_oqtopus.qiskit import (
    OpenQARPOqtopusSamplerV2,
    OpenQARPOqtopusEstimatorV2,
)

client = OqtopusClient(OqtopusConfig.from_file("default", path="~/.oqtopus"))
sampler = OpenQARPOqtopusSamplerV2(device_id="qulacs", client=client, n_shots=1000)
bell = QuantumCircuit(2)
bell.h(0)
bell.cx(0, 1)
measured = bell.copy()
measured.measure_all()

result = sampler.run([measured]).result()
print(result[0].data.meas.get_counts())  # {"00": 約500, "11": 約500}

estimator = OpenQARPOqtopusEstimatorV2(device_id="qulacs", client=client)
observable = SparsePauliOp.from_list([("ZZ", 1.0), ("XX", 0.5)])
estimate = estimator.run([(bell, observable)], precision=0.05).result()[0]
print(estimate.data.evs, estimate.data.stds)  # 1.5, 0.0
```

`run` はジョブを返し、`result()` が完了を待ちます。パラメータ付き PUB と配列 broadcasting に対応し、shots は PUB の値 > `run(shots=...)` > constructor の順です。回路はパラメータ束縛後に QASM 3 へシリアライズします。ローカルの transpile・ゲート分解・routing・配置変更は行いません。

Sampler の結果は古典レジスタごとの `BitArray` です。`QuantumCircuit(2, 2)` の既定レジスタは `data.c`、`measure_all()` が追加するレジスタは `data.meas` です。整数の bit i は古典ビット i に対応し、文字列は bit 0 が右端です。任意の終端測定写像と複数レジスタに対応します。OQTOPUS が返す集計 counts を展開するため、元の shot 実行順は復元しません。複数レジスタは同じ joint outcome 列から射影して相関を保ちます。

Estimator は Pauli 項ごとに測定回路を生成します。X/Y の基底測定に必要な H/Sdg と終端測定を追加し、終端測定済みの入力ではその測定を置き換えます。precision の既定値は `1/sqrt(n_shots)`、各項の shots は `ceil(sum(coeff**2)/precision**2)`（最小1）です。10,000を超える要求は送信前に拒否します。`stds` は counts から推定する独立測定の標準誤差です。precision は統計的な目標で、デバイスノイズや系統誤差を含みません。恒等項だけの計算はジョブを送信しません。

同じインスタンスのジョブは順次実行されます。`job.cancel()` は開始前のローカル待機を取消すもので、実行中のクラウドジョブを取消しません。クラウド job_id は結果の metadata と `sampler.jobs` / `estimator.jobs` に残ります。

## セットアップ

Python 3.12 と uv を使用します。macOS では Command Line Tools 等の C++20 コンパイラが必要です。初回ビルドでは PyPI と CMake の公開依存ソースを取得します。

すでに `ref` がある場合、クローン操作は不要です。新規環境では次を実行します。

```sh
mkdir -p ref
git clone https://github.com/OpenQARP/openqarp.git ref/openqarp
git -C ref/openqarp checkout 6c632b9a66383584a5944685abb81d33900c5836
git clone https://github.com/oqtopus-team/oqtopus-client.git ref/oqtopus-client
git -C ref/oqtopus-client checkout d0b3d53c15025958197190b644efafdb06d202b1
```

リポジトリのルートで実行してください。

```sh
CMAKE_BUILD_PARALLEL_LEVEL=3 uv sync --locked --extra qiskit
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python scripts/local_smoke.py
```

uv が `.venv` を管理し、このパッケージと両クローンを editable install します。この環境を別の OpenQARP チェックアウトと共用しないでください。`uv.toml` でキャッシュもワークスペース内に置きます。C++ を変更した後は `CMAKE_BUILD_PARALLEL_LEVEL=3 uv sync --locked --reinstall-package openqarp` で再ビルドします。

`ref/openqarp`、`ref/oqtopus-client`、環境・キャッシュ・実行生成物は親の Git 管理対象外です。各クローン自身の Git 履歴は保持します。`uv.lock` は Python 依存を固定しますが、editable ソースは固定しないため、参照コミットは上記と計画書で記録します。

## 動作確認

通常のアダプタテストは認証・ネットワークなしで実行できます。

```sh
MPLCONFIGDIR=artifacts/matplotlib uv run --locked --extra qiskit python -m pytest -q
```

`scripts/local_smoke.py` は Bell 状態の測定、厳密期待値、q0/q1 の非対称回路、OpenQASM 3 出力、SDK ジョブ入力の生成を検証します。`artifacts/local-smoke.json` に実行結果と参照コミット、`artifacts/bell.qasm` に QASM を保存します。OQTOPUS へのジョブ送信は行いません。

OpenQARP の関連テスト:

```sh
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python -m pytest \
  -c ref/openqarp/pytest.ini \
  ref/openqarp/tests/test_algorithms/test_primitives/test_sampler.py \
  ref/openqarp/tests/test_devices/test_device.py \
  ref/openqarp/tests/test_postselection.py -q
```

## OQTOPUS 接続確認

接続先は `qulacs` を使用します。既存の `/Users/tmori/temp/oqt-cloud/a.py` は quri-parts-oqtopus を使用していますが、同じ設定を oqtopus-client でも利用できることを確認しました。設定ファイルはコピーせず、明示的に指定します。

```sh
uv run --locked python scripts/check_oqtopus_connection.py \
  --config ~/.oqtopus --section default --device-id qulacs
```

2026-10-28 にデバイス情報の取得に成功しました。`qulacs` は `simulator`、`available`、16量子ビットです。この確認はジョブを送信しません。設定の値やトークンは出力しません。

## Sampler / Estimator の利用

以下はクラウドにジョブを送信します。

```python
from oqtopus_client import OqtopusClient, OqtopusConfig
from qarp.blocks import SimpleBlock
from qarp.operators import QubitOperator
from openqarp_oqtopus import OpenQARPOqtopusSampler, OpenQARPOqtopusEstimator

client = OqtopusClient(OqtopusConfig.from_file("default", path="~/.oqtopus"))
sampler = OpenQARPOqtopusSampler(device_id="qulacs", client=client, n_shots=1000)
bell = SimpleBlock(2)
bell.h(0)
bell.cx(0, 1)

distribution = sampler.sample(ket=bell)
print(dict(distribution))  # {(0, 0): 約0.5, (1, 1): 約0.5}

estimator = OpenQARPOqtopusEstimator(sampler=sampler)
value = estimator.estimate(ket=bell, operator=QubitOperator("Z0 Z1"))
print(value)  # 1.0
print(sampler.jobs)  # job_id・接続先・shots・状態の履歴
```

`device_id` は必須で、生成後は読取専用です。別の接続先を使う場合は別インスタンスを作ります。`sample` は OpenQARP の `SamplingDistribution` を返し、キーは qubit 0 が先頭の LSB 順タプル、値は確率です。`params={symbol: value}`、`n_shots=...`、`measured_qubits=[...]` を指定できます。Estimator の shots は測定グループごとの回数です。

標準の OpenQARP プリミティブを使う場合は `sampler.executor.build([...])` / `run(params=...)` / `batch_run(...)` が使えます。`batch_run` の結果は `[parameter_set][primitive]` の順です。すべて同期実行で、同一インスタンスの並行呼出しは非対応です。

OpenQARP の命令列は QASM 3 としてそのまま出力します。トランスパイルはサーバに任せ、既存接続例と同じ Qiskit 設定（最適化レベル2）をジョブに渡します。追加のサーバ設定は生成時に `transpiler_info=...` で指定できます。対応命令・量子ビット容量・topology・配置のクライアント側判定は行いません。OpenQARP API では測定のない回路に終端測定を追加します。この API の EXACT・状態ベクトル・初期状態の直接注入・中間測定・部分的/非同番号の古典ビットへの測定・勾配は未対応です。エラー緩和は有効にしません。

ジョブの失敗・取消・待機タイムアウトでは `OqtopusJobError`、不正な counts では `OqtopusResultError` を返します。登録後に得られた job_id は例外と `sampler.jobs` に残ります。元の SDK 例外は `__cause__` で参照できます。タイムアウトは遠隔ジョブの取消しを意味しません。自動再送はせず、必要なら `client.get_job_result(job_id)` で再取得できます。SDK の submit_job 内部で登録・アップロードに失敗した場合は job_id を得られないことがあります。

## クラウドでの検証例

次のコマンドは `qulacs` に各回路1,000 shotsのジョブを送ります。sampling は X0 / X1 / Bell の3回路、estimation は二つの期待値回路と Ry の3点 sweep です。通常テストからは実行されません。

```sh
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python examples/oqtopus_sampling.py \
  --config ~/.oqtopus --device-id qulacs
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python examples/oqtopus_estimation.py \
  --config ~/.oqtopus --device-id qulacs
MPLCONFIGDIR=artifacts/matplotlib uv run --locked --extra qiskit python examples/qiskit_cloud.py \
  --config ~/.oqtopus --device-id qulacs
```

結果とジョブIDは `artifacts/remote-sampling.json` / `artifacts/remote-estimation.json` に保存します。2026-10-28 の sampling 検証では X0=`(1,0)`、X1=`(0,1)` に確率1、Bell は `(0,0)=0.521` / `(1,1)=0.479` でした。整数キーへの変換に追加のビット反転は不要でした。Estimator は Bell の `ZZ+0.5XX=1.5`、X0 状態の `Z0−2Z1=−3.0`、Ry(0,π/3,π) の P(1) は `0, 0.255, 1` で、全8ジョブが成功しました。オフラインのアダプタ試験37件、関連 upstream 試験90件も成功しています。

Qiskit の検証は `artifacts/remote-qiskit.json` に保存します。2026-10-28 にローカル transpile なしで4ジョブが成功しました。Bell は `00=510, 11=490`、古典ビットの対応を入れ替えた回路は `low=0, high=1` に各1,000回、期待値は `1.5`、標準誤差は `0.0` でした。

現在のテストは62件すべて成功しています。別の uv 環境に wheel を通常インストールし、開発元の外から同じ62件が通ることも確認しました。Qiskit extra を入れない別環境では44件成功、Qiskit テスト1モジュールが skip となり、基本 API の import と動作を確認できています。
