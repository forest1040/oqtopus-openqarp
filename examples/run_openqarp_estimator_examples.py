"""Submit upstream expectation-value examples as native OQTOPUS estimation jobs."""

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import platform
import subprocess
import time

import numpy as np
from oqtopus_client import OqtopusClient, OqtopusConfig
import qarpx as qx

from openqarp_oqtopus import OpenQARPOqtopusEstimator, OpenQARPOqtopusSampler
from _openqarp_estimator_cases import estimator_cases


ROOT = Path(__file__).resolve().parents[1]
TEXT = {
    "hea-linear": (
        "HEA回路の期待値（パラメータlinspace）",
        "元の3量子ビットHEA回路に、正規順序のパラメータをlinspace(0,1,6)で束縛し、native Estimatorへの送信対象とした。",
    ),
    "hea-constant": (
        "HEA回路の期待値（全パラメータ0.1）",
        "元例の回路とobservableを使用。項ごとのHadamard測定を1つのnative estimationジョブに変更。元例の20,000 shotsを本レポートの実行条件に記載したshots数へ変更した。",
    ),
    "basis-pauli": (
        "計算基底状態のPauli和",
        "元セルの状態(1,1,0,1)とPauli和をそのまま送信。ローカルのPauliAveragingをnative Estimatorに置き換えた。解析値は1−(−1)+0=2。",
    ),
    "tfim-vqe": (
        "3量子ビットIsingモデルのVQE最終状態",
        "元ノートブックのCOBYLA最適化をローカルの厳密StateVectorで実行し、最終回路とHamiltonian全体を1つのnative estimationジョブへ送信。最適化ループ全体のクラウド実行ではない。",
    ),
}


def reference(case):
    block = case["block"]
    block.build()
    width = block.n_qubits
    if not 1 <= width <= 16:
        raise ValueError("Selected circuit exceeds the 16-qubit scope")
    params = {str(key): float(value) for key, value in case["params"].items()}
    commands = qx.substitute_all(block.flatten(), params)
    psi = np.asarray(qx.QarpSimulator().statevector(commands, width))
    indices = np.arange(1 << width)
    hpsi = np.zeros_like(psi)
    terms = []
    for term, coefficient in case["operator"].terms.items():
        target, phase = indices.copy(), np.ones(len(indices), dtype=complex)
        for qubit, axis in term:
            if axis in ("Z", "Y"):
                phase *= (1 - 2 * ((indices >> qubit) & 1)) * (1j if axis == "Y" else 1)
            if axis in ("X", "Y"):
                target ^= 1 << qubit
        hpsi[target] += complex(coefficient) * phase * psi
        terms.append({"pauli": " ".join(f"{axis} {qubit}" for qubit, axis in term) or "I 0",
                      "coefficient": float(complex(coefficient).real)})
    expectation = np.vdot(psi, hpsi)
    if abs(expectation.imag) > 1e-10:
        raise ValueError("Non-real expectation for a Hermitian observable")
    return width, commands, float(expectation.real), terms


def markdown(report):
    records = report["cases"]
    successful = sum(r.get("status") == "succeeded" for r in records)
    lines = [
        "# OpenQARP examples — native Estimator 実行結果", "",
        f"実行開始: {report['started_at']}。接続先: `{report['device_id']}`。",
        f"native estimationの成功: **{successful}/{len(records)}**。", "",
        "## 対象と実行条件", "",
        "OpenQARPのStateVector / PauliAveragingによる期待値例を、OQTOPUSのnative Estimatorへ接続した。",
        "bra=ket のHermitian期待値を対象とし、別のbraとの重なり・遷移振幅は含めない。", "",
        f"- Python `{report['python']}`、OpenQARP commit `{report['openqarp_commit']}`。",
        f"- Adapterの基準commit `{report['adapter_commit']}`。実行用スクリプトは本作業で追加。",
        f"- 各ケース1つのestimationジョブ、{report['shots']:,} shots、タイムアウト{report['timeout']}秒。",
        "- 回路は3〜4量子ビットで、すべて16量子ビット以内。",
        "- 入力回路のQASMとobservable全項をそのまま送信。分解・グルーピング・測定回路追加・期待値集計はサーバ側。",
        "- ローカル参照値は元回路の厳密statevectorとNumPyによるPauli作用の計算から求めた。クラウドの戻り値の代用にはしていない。",
        "- `server stds`はサーバ返却値をそのまま記録し、クライアント側で再計算しない。",
        "- VQEの最適化はローカル。クラウドでは最終回路の期待値だけを評価。", "",
        "## 再現コマンド", "", "```sh",
        "MPLCONFIGDIR=artifacts/matplotlib uv run --locked --extra qiskit python examples/run_openqarp_estimator_examples.py \\",
        f"  --config ~/.oqtopus --device-id {report['device_id']} --shots {report['shots']}",
        "```", "", "## 一覧", "",
        "| ケース | qubits | Pauli項数 | ローカル参照値 | サーバ期待値 | server stds | 絶対差 | 結果 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for record in records:
        def number(key):
            value = record.get(key)
            return f"{value:.10f}" if isinstance(value, (float, int)) else "—"
        title = TEXT[record["id"]][0]
        lines.append(f"| {title} | {record.get('n_qubits','—')} | {len(record.get('operator',[]))} | {number('reference')} | {number('exp_value')} | {number('stds')} | {number('absolute_error')} | {record.get('status','未実行')} |")
    lines.extend(["", "失敗したケースについては、期待値が確認できたという判定を行わない。",
                  "一覧の参照値は計算内容の照合用であり、サーバ結果欄への補完は行っていない。", ""])
    if any(r["id"] == "basis-pauli" and "number of qubits" in (r.get("server_message") or "")
           for r in records):
        lines.extend(["## 確認したサーバ側エラー", "",
                      "計算基底状態のケースでは、送信QASMに4量子ビットを宣言し、q0・q1・q3にX操作を含めたが、",
                      "サーバは量子ビット数3としてobservableのindex 3を拒否した。該当ジョブと応答は下記に記録する。",
                      "入力の量子ビットやobservableの補正、ローカル計算へのフォールバックは行っていない。", ""])
    for record in records:
        title, scope = TEXT[record["id"]]
        source = record['source'].removeprefix('ref/openqarp/')
        url = f"https://github.com/OpenQARP/openqarp/blob/{report['openqarp_commit']}/{source}"
        lines.extend([f"## {title}", "", f"出典: [{Path(source).name}]({url})、セル{record['cells']}（0始まり）。",
                      "", scope, "", "設定:", "", "```json",
                      json.dumps(record["settings"], ensure_ascii=False, indent=2), "```", "",
                      "送信するobservable全体:", "", "```json",
                      json.dumps(record.get("operator", []), ensure_ascii=False, indent=2), "```", ""])
        if "reference" in record:
            lines.append(f"- ローカル厳密期待値: `{record['reference']:.12f}`")
        for job in record.get("jobs", []):
            lines.append(f"- job_id: `{job['job_id']}` / status: `{job['status']}` / shots: {job['n_shots']:,}")
        lines.append(f"- 送信・待機・取得時間: `{record.get('remote_seconds',0):.2f}` 秒")
        if record.get("server_message"):
            lines.extend(["", "サーバ応答:", "", "```text", record["server_message"], "```"])
        if record.get("error_type"):
            lines.append(f"- 例外: `{record['error_type']}`。自動再送・回路の補正は行っていない。")
        if "exp_value" in record:
            lines.extend([f"- サーバ期待値: `{record['exp_value']}`",
                          f"- サーバstds: `{record.get('stds', '取得失敗')}`",
                          f"- 参照との差: `{record['absolute_error']}`"])
        lines.append("")
    lines.extend(["## 保存物と実行範囲", "",
                  "- 数値結果・ジョブID・元回路QASM: `artifacts/openqarp-estimator-examples/`（Git管理外）。",
                  "- 認証情報は既存ファイルから読み込み、出力・コピーしていない。",
                  "- 参照ノートブックとライブラリ実装は変更していない。", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--section", default="default")
    parser.add_argument("--device-id", default="qulacs")
    parser.add_argument("--shots", type=int, default=2000)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--render", type=Path)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/openqarp-estimator-examples-results.md")
    args = parser.parse_args()
    if args.render:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown(json.loads(args.render.read_text())))
        return 0
    if not 1 <= args.shots <= 10000:
        parser.error("shots must be between 1 and 10000")
    if not args.config and not args.local_only:
        parser.error("--config is required for Cloud execution")
    artifacts = ROOT / "artifacts/openqarp-estimator-examples"
    artifacts.mkdir(parents=True, exist_ok=True)
    logs = io.StringIO()
    with redirect_stdout(logs):
        cases = estimator_cases()
    (artifacts / "preparation.log").write_text(logs.getvalue())
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
        "device_id": args.device_id, "shots": args.shots, "timeout": args.timeout,
        "openqarp_commit": subprocess.check_output(["git", "-C", str(ROOT / "ref/openqarp"), "rev-parse", "HEAD"], text=True).strip(),
        "adapter_commit": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "cases": [],
    }
    client = None if args.local_only else OqtopusClient(OqtopusConfig.from_file(args.section, path=args.config))
    for case in cases:
        record = {k: v for k, v in case.items() if k not in ("block", "operator", "params")}
        report["cases"].append(record)
        sampler = None
        started = time.monotonic()
        try:
            width, commands, expected, operator = reference(case)
            record.update(n_qubits=width, commands=len(commands), reference=expected, operator=operator,
                          bindings={str(k): float(v) for k, v in case["params"].items()})
            record["settings"]["bindings"] = record["bindings"]
            (artifacts / f"{case['id']}.qasm").write_text(qx.QASM3Emitter().emit(commands, width))
            print(json.dumps({"case": case["id"], "qubits": width, "reference": expected, "stage": "local-verified"}), flush=True)
            if args.local_only:
                record["status"] = "local_only"
                continue
            sampler = OpenQARPOqtopusSampler(device_id=args.device_id, client=client, n_shots=args.shots, timeout=args.timeout)
            estimator = OpenQARPOqtopusEstimator(sampler=sampler)
            started = time.monotonic()
            value = estimator.estimate(ket=case["block"], operator=case["operator"], params=case["params"])
            record.update(status="succeeded", exp_value=float(value), absolute_error=abs(value - expected))
            try:
                result = client.get_job_result(sampler.jobs[-1].job_id)
                record["stds"] = float(result.stds)
            except Exception as retrieval_error:
                record.update(status="stds_read_failed", error_type=type(retrieval_error).__name__)
        except Exception as error:
            record.update(status="failed", error_type=type(error).__name__)
            job_id = getattr(error, "job_id", None)
            if client is not None and job_id:
                try:
                    record["server_message"] = client.get_job_result(job_id).message
                except Exception as retrieval_error:
                    record["message_error_type"] = type(retrieval_error).__name__
        finally:
            if sampler is not None:
                record["jobs"] = [asdict(job) for job in sampler.jobs]
                record["remote_seconds"] = time.monotonic() - started
            args.output.parent.mkdir(parents=True, exist_ok=True)
            (artifacts / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            args.output.write_text(markdown(report))
            print(json.dumps({"case": case["id"], "status": record["status"], "value": record.get("exp_value"), "error": record.get("error_type"), "job_ids": [j["job_id"] for j in record.get("jobs", [])]}), flush=True)
    return 0 if args.local_only or all(c.get("status") == "succeeded" for c in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
