# OpenQARP examples — native Estimator 実行結果

実行開始: 2026-10-28T05:35:42.976389+00:00。接続先: `qulacs`。
native estimationの成功: **3/4**。

## 対象と実行条件

OpenQARPのStateVector / PauliAveragingによる期待値例を、OQTOPUSのnative Estimatorへ接続した。
bra=ket のHermitian期待値を対象とし、別のbraとの重なり・遷移振幅は含めない。

- Python `3.12.15`、OpenQARP commit `6c632b9a66383584a5944685abb81d33900c5836`。
- Adapterの基準commit `edec3cc6006ff6a68bd71ec3e0fca9568eb8b6f1`。実行用スクリプトは本作業で追加。
- 各ケース1つのestimationジョブ、2,000 shots、タイムアウト300秒。
- 回路は3〜4量子ビットで、すべて16量子ビット以内。
- 入力回路のQASMとobservable全項をそのまま送信。分解・グルーピング・測定回路追加・期待値集計はサーバ側。
- ローカル参照値は元回路の厳密statevectorとNumPyによるPauli作用の計算から求めた。クラウドの戻り値の代用にはしていない。
- `server stds`はサーバ返却値をそのまま記録し、クライアント側で再計算しない。
- VQEの最適化はローカル。クラウドでは最終回路の期待値だけを評価。

## 再現コマンド

```sh
MPLCONFIGDIR=artifacts/matplotlib uv run --locked --extra qiskit python examples/run_openqarp_estimator_examples.py \
  --config ~/.oqtopus --device-id qulacs --shots 2000
```

## 一覧

| ケース | qubits | Pauli項数 | ローカル参照値 | サーバ期待値 | server stds | 絶対差 | 結果 |
|---|---:|---:|---:|---:|---:|---:|---|
| HEA回路の期待値（パラメータlinspace） | 3 | 2 | -0.3592226466 | -0.3759999871 | 0.0263109189 | 0.0167773405 | succeeded |
| HEA回路の期待値（全パラメータ0.1） | 3 | 2 | -0.9265475486 | -0.9480000138 | 0.0150081180 | 0.0214524652 | succeeded |
| 計算基底状態のPauli和 | 4 | 3 | 2.0000000000 | — | — | — | failed |
| 3量子ビットIsingモデルのVQE最終状態 | 3 | 5 | -2.4032119240 | -2.3540000916 | 0.0563625395 | 0.0492118325 | succeeded |

失敗したケースについては、期待値が確認できたという判定を行わない。
一覧の参照値は計算内容の照合用であり、サーバ結果欄への補完は行っていない。

## 確認したサーバ側エラー

計算基底状態のケースでは、送信QASMに4量子ビットを宣言し、q0・q1・q3にX操作を含めたが、
サーバは量子ビット数3としてobservableのindex 3を拒否した。該当ジョブと応答は下記に記録する。
入力の量子ビットやobservableの補正、ローカル計算へのフォールバックは行っていない。

## HEA回路の期待値（パラメータlinspace）

出典: [tutorial_02_primitives.ipynb](https://github.com/OpenQARP/openqarp/blob/6c632b9a66383584a5944685abb81d33900c5836/examples/tutorial_02_primitives.ipynb)、セル[2, 4]（0始まり）。

元の3量子ビットHEA回路に、正規順序のパラメータをlinspace(0,1,6)で束縛し、native Estimatorへの送信対象とした。

設定:

```json
{
  "n_qubits": 3,
  "layers": 2,
  "bindings": {
    "ry_0_0": 0.0,
    "ry_0_1": 0.2,
    "ry_0_2": 0.4,
    "ry_1_0": 0.6000000000000001,
    "ry_1_1": 0.8,
    "ry_1_2": 1.0
  },
  "n_parameters": 6
}
```

送信するobservable全体:

```json
[
  {
    "pauli": "Z 0 Z 1",
    "coefficient": -1.0
  },
  {
    "pauli": "X 2",
    "coefficient": 0.5
  }
]
```

- ローカル厳密期待値: `-0.359222646615`
- job_id: `06ae189b-2995-7bc7-8000-885675e8c54f` / status: `succeeded` / shots: 2,000
- 送信・待機・取得時間: `27.39` 秒
- サーバ期待値: `-0.37599998712539673`
- サーバstds: `0.026310918852686882`
- 参照との差: `0.01677734051085361`

## HEA回路の期待値（全パラメータ0.1）

出典: [tutorial_02_primitives.ipynb](https://github.com/OpenQARP/openqarp/blob/6c632b9a66383584a5944685abb81d33900c5836/examples/tutorial_02_primitives.ipynb)、セル[2, 9]（0始まり）。

元例の回路とobservableを使用。項ごとのHadamard測定を1つのnative estimationジョブに変更。元例の20,000 shotsを本レポートの実行条件に記載したshots数へ変更した。

設定:

```json
{
  "n_qubits": 3,
  "layers": 2,
  "parameter_value": 0.1,
  "notebook_shots": 20000,
  "bindings": {}
}
```

送信するobservable全体:

```json
[
  {
    "pauli": "Z 0 Z 1",
    "coefficient": -1.0
  },
  {
    "pauli": "X 2",
    "coefficient": 0.5
  }
]
```

- ローカル厳密期待値: `-0.926547548611`
- job_id: `06ae189c-c732-7eab-8000-97ceba6d7990` / status: `succeeded` / shots: 2,000
- 送信・待機・取得時間: `19.97` 秒
- サーバ期待値: `-0.9480000138282776`
- サーバstds: `0.015008118003606796`
- 参照との差: `0.021452465216974748`

## 計算基底状態のPauli和

出典: [mwe_primitive_algorithms.ipynb](https://github.com/OpenQARP/openqarp/blob/6c632b9a66383584a5944685abb81d33900c5836/examples/algorithms/primitives/mwe_primitive_algorithms.ipynb)、セル[15]（0始まり）。

元セルの状態(1,1,0,1)とPauli和をそのまま送信。ローカルのPauliAveragingをnative Estimatorに置き換えた。解析値は1−(−1)+0=2。

設定:

```json
{
  "n_qubits": 4,
  "basis_bits_lsb_first": [
    1,
    1,
    0,
    1
  ],
  "analytic_expectation": 2.0,
  "bindings": {}
}
```

送信するobservable全体:

```json
[
  {
    "pauli": "Z 0 Z 1",
    "coefficient": 1.0
  },
  {
    "pauli": "Z 2 Z 3",
    "coefficient": -1.0
  },
  {
    "pauli": "Z 0 Y 1 X 3",
    "coefficient": 1.0
  }
]
```

- ローカル厳密期待値: `2.000000000000`
- job_id: `06ae189d-fd3d-7226-8000-6159b5500633` / status: `failed` / shots: 2,000
- 送信・待機・取得時間: `11.29` 秒

サーバ応答:

```text
<AioRpcError of RPC that terminated with:
	status = StatusCode.INVALID_ARGUMENT
	details = "The specified operator is invalid. The number of qubits (3) is smaller than a required index 3."
	debug_error_string = "UNKNOWN:Error received from peer  {grpc_message:"The specified operator is invalid. The number of qubits (3) is smaller than a required index 3.", grpc_status:3}"
>
```
- 例外: `OqtopusJobError`。自動再送・回路の補正は行っていない。

## 3量子ビットIsingモデルのVQE最終状態

出典: [tutorial_04_variational_loop.ipynb](https://github.com/OpenQARP/openqarp/blob/6c632b9a66383584a5944685abb81d33900c5836/examples/tutorial_04_variational_loop.ipynb)、セル[1, 3, 5, 7]（0始まり）。

元ノートブックのCOBYLA最適化をローカルの厳密StateVectorで実行し、最終回路とHamiltonian全体を1つのnative estimationジョブへ送信。最適化ループ全体のクラウド実行ではない。

設定:

```json
{
  "n_qubits": 3,
  "layers": 2,
  "field": 0.5,
  "seed": 42,
  "optimizer": "COBYLA",
  "maxiter": 400,
  "objective_evaluations": 240,
  "local_variational_energy": -2.4032119240392706,
  "exact_ground_energy": -2.4032119259115525,
  "optimized_parameters": [
    3.141642034412733,
    3.1415436161482004,
    5.856880029250869,
    3.8614633777445553,
    1.5708853745432272,
    6.810059510466622
  ],
  "bindings": {
    "ry_0_0": 3.141642034412733,
    "ry_0_1": 3.1415436161482004,
    "ry_0_2": 5.856880029250869,
    "ry_1_0": 3.8614633777445553,
    "ry_1_1": 1.5708853745432272,
    "ry_1_2": 6.810059510466622
  }
}
```

送信するobservable全体:

```json
[
  {
    "pauli": "Z 0 Z 1",
    "coefficient": -1.0
  },
  {
    "pauli": "Z 1 Z 2",
    "coefficient": -1.0
  },
  {
    "pauli": "X 0",
    "coefficient": -0.5
  },
  {
    "pauli": "X 1",
    "coefficient": -0.5
  },
  {
    "pauli": "X 2",
    "coefficient": -0.5
  }
]
```

- ローカル厳密期待値: `-2.403211924039`
- job_id: `06ae189e-b1bb-7a27-8000-d4eb991af4ee` / status: `succeeded` / shots: 2,000
- 送信・待機・取得時間: `10.90` 秒
- サーバ期待値: `-2.3540000915527344`
- サーバstds: `0.056362539529800415`
- 参照との差: `0.04921183248653627`

## 保存物と実行範囲

- 数値結果・ジョブID・元回路QASM: `artifacts/openqarp-estimator-examples/`（Git管理外）。
- 認証情報は既存ファイルから読み込み、出力・コピーしていない。
- 参照ノートブックとライブラリ実装は変更していない。
