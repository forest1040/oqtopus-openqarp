# OpenQARP / OQTOPUS 検証ワークスペース

OpenQARP をローカル CPU で検証し、oqtopus-client を使う接続アダプタを設計するためのリポジトリです。Sampler / Estimator の実装計画は [docs/oqtopus-integration-plan.md](docs/oqtopus-integration-plan.md) にあります。

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

uv が `.venv` を管理し、両クローンを editable install します。この環境を別の OpenQARP チェックアウトと共用しないでください。`uv.toml` でキャッシュもワークスペース内に置きます。C++ を変更した後は `CMAKE_BUILD_PARALLEL_LEVEL=3 uv sync --locked --reinstall-package openqarp` で再ビルドします。

`ref/openqarp`、`ref/oqtopus-client`、環境・キャッシュ・実行生成物は親の Git 管理対象外です。各クローン自身の Git 履歴は保持します。`uv.lock` は Python 依存を固定しますが、editable ソースは固定しないため、参照コミットは上記と計画書で記録します。

## 動作確認

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

公開 API 案では `OpenQARPOqtopusSampler(device_id="qulacs", client=client)` に接続先を指定し、`OpenQARPOqtopusEstimator(sampler=sampler)` が同じ接続を使います。QASM のリモート実行と結果ビット順の検証は次段階です。
