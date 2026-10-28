# OpenQARP / OQTOPUS Cloud アダプタ

OpenQARP の回路を oqtopus-client で OQTOPUS Cloud に送信し、測定分布・期待値を取得します。Sampler / Estimator の設計・検証記録は [docs/oqtopus-integration-plan.md](docs/oqtopus-integration-plan.md) にあります。内部実行層は `_OqtopusCloudExecutor` です。

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
CMAKE_BUILD_PARALLEL_LEVEL=3 uv sync --locked
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python scripts/local_smoke.py
```

uv が `.venv` を管理し、このパッケージと両クローンを editable install します。この環境を別の OpenQARP チェックアウトと共用しないでください。`uv.toml` でキャッシュもワークスペース内に置きます。C++ を変更した後は `CMAKE_BUILD_PARALLEL_LEVEL=3 uv sync --locked --reinstall-package openqarp` で再ビルドします。

`ref/openqarp`、`ref/oqtopus-client`、環境・キャッシュ・実行生成物は親の Git 管理対象外です。各クローン自身の Git 履歴は保持します。`uv.lock` は Python 依存を固定しますが、editable ソースは固定しないため、参照コミットは上記と計画書で記録します。

## 動作確認

通常のアダプタテストは認証・ネットワークなしで実行できます。

```sh
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python -m pytest -q
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

QASM は標準ゲートに分解して出力し、既存接続例と同じ Qiskit 設定（最適化レベル2）でサーバ側の変換を依頼します。追加設定は生成時に `transpiler_info=...` で指定できます。測定のない回路には終端測定を追加します。EXACT・状態ベクトル・初期状態の直接注入・中間測定・部分的/非同番号の古典ビットへの測定・勾配は未対応です。エラー緩和は有効にしません。

ジョブの失敗・取消・待機タイムアウトでは `OqtopusJobError`、不正な counts では `OqtopusResultError` を返します。登録後に得られた job_id は例外と `sampler.jobs` に残ります。元の SDK 例外は `__cause__` で参照できます。タイムアウトは遠隔ジョブの取消しを意味しません。自動再送はせず、必要なら `client.get_job_result(job_id)` で再取得できます。SDK の submit_job 内部で登録・アップロードに失敗した場合は job_id を得られないことがあります。

## クラウドでの検証例

次のコマンドは `qulacs` に各回路1,000 shotsのジョブを送ります。sampling は X0 / X1 / Bell の3回路、estimation は二つの期待値回路と Ry の3点 sweep です。通常テストからは実行されません。

```sh
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python examples/oqtopus_sampling.py \
  --config ~/.oqtopus --device-id qulacs
MPLCONFIGDIR=artifacts/matplotlib uv run --locked python examples/oqtopus_estimation.py \
  --config ~/.oqtopus --device-id qulacs
```

結果とジョブIDは `artifacts/remote-sampling.json` / `artifacts/remote-estimation.json` に保存します。2026-10-28 の sampling 検証では X0=`(1,0)`、X1=`(0,1)` に確率1、Bell は `(0,0)=0.521` / `(1,1)=0.479` でした。整数キーへの変換に追加のビット反転は不要でした。Estimator は Bell の `ZZ+0.5XX=1.5`、X0 状態の `Z0−2Z1=−3.0`、Ry(0,π/3,π) の P(1) は `0, 0.255, 1` で、全8ジョブが成功しました。オフラインのアダプタ試験37件、関連 upstream 試験90件も成功しています。
