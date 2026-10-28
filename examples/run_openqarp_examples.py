"""Run selected upstream notebook circuits on OQTOPUS and save a Markdown report."""

import argparse
from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
import io
import json
from math import log, sqrt
from pathlib import Path
import platform
import subprocess
import time

import numpy as np
from oqtopus_client import OqtopusClient, OqtopusConfig
import qarpx as qx

from openqarp_oqtopus import OpenQARPOqtopusSampler
from _openqarp_algorithm_cases import algorithm_cases
from _openqarp_state_cases import state_cases


ROOT = Path(__file__).resolve().parents[1]

DESCRIPTIONS = {
    "grover": (
        "Grover探索",
        "元ノートブックの2量子ビットoracleと1回の振幅増幅を使用。EXACT指定を有限shotsに変更した。",
        "正解は整数2（LSB先頭のbit列01）。理論上の成功確率は1。",
    ),
    "uniform-superposition-11-of-16": (
        "11状態の一様重ね合わせ",
        "元のUniformSuperpositionBlock(M=11)をそのまま測定。描画と状態ベクトル表示を省略した。",
        "整数0〜10に各1/11、11〜15に確率0。",
    ),
    "sparse-state-8q": (
        "疎な複素振幅状態の準備（8量子ビット）",
        "元セルのseed=1と2つの複素振幅を再現。dense合成とのコスト比較部分は実行せず、生成状態を測定した。",
        "入力振幅の絶対値二乗を正規化すると、整数206に0.7727151385、50に0.2272848615。測定確率だけでは相対位相は検証できない。",
    ),
    "fermi-hubbard-trotter-one-particle": (
        "Fermi-HubbardモデルのTrotter発展",
        "元ノートブックの2サイトHamiltonianとtime=1、steps=1、order=2を使用。真空からの変化を避けるため、初期状態として1電子状態(1,0,0,0)と終端測定を明示的に追加した。",
        "1電子部分空間では相互作用項は作用せず、整数1の確率はcos²(0.14)、4の確率はsin²(0.14)。",
    ),
    "qrom-superposed-lookup": (
        "QROMの重ね合わせ検索",
        "元セルの2量子ビットindexの一様重ね合わせと3ビットのlookup表を使用。入力indexと出力データを同時に測定した。",
        "表に対応する整数14、19、20、25の確率はそれぞれ1/4。",
    ),
    "qaoa": (
        "QAOAによる重み付きMaxCut",
        "元ノートブックと同じseed=1234、4頂点2辺、辺重み2、2層でローカル最適化を実行。クラウドには最適化後の束縛済み回路だけを送信し、最適化ループ全体をクラウド実行したわけではない。",
        "全16状態の独立な列挙で最適カット重みは4、最適状態は整数1、3、12、14。確率分布はローカル厳密実行と比較し、最適状態の合計確率と平均カット重みも算出する。",
    ),
    "qpe": (
        "QPE：2サイトFermi-Hubbardモデル",
        "元の物理4量子ビット＋ancilla5量子ビット、Trotter4ステップ、order=2、time=−2πを使用。部分測定を等価な全測定＋ancilla周辺化に変更。位相の曲線フィッティングと描画は省略した。",
        "全占有状態|1111>の固有エネルギーは2U＋shift=0.6493865299。5ancillaのDirichlet分布と照合し、最頻binは21/32=0.65625付近を期待する。",
    ),
}


def case_text(record):
    return DESCRIPTIONS.get(record["id"], (
        record["title"], record["execution_scope"], record["expected_description"],
    ))


def local_probabilities(case):
    block = case["block"]
    block.build()
    width = block.n_qubits
    if not 1 <= width <= 16:
        raise ValueError(f"Case {case['id']} requires {width} qubits")
    commands = qx.substitute_all(block.flatten(), {str(k): float(v) for k, v in case["params"].items()})
    unitary = [c for c in commands if c.gate not in (qx.GateType.Measure, qx.GateType.Barrier)]
    vector = np.asarray(qx.QarpSimulator().statevector(unitary, width))
    measured = case["measured_qubits"] or list(range(width))
    packed = np.zeros(1 << width, dtype=np.int64)
    indices = np.arange(1 << width)
    for position, qubit in enumerate(measured):
        packed |= ((indices >> qubit) & 1) << position
    marginal = np.bincount(packed, weights=np.abs(vector) ** 2, minlength=1 << len(measured))
    return width, commands, measured, {i: float(p) for i, p in enumerate(marginal) if p > 1e-14}


def total_variation(left, right):
    return 0.5 * sum(abs(left.get(k, 0.0) - right.get(k, 0.0)) for k in left.keys() | right.keys())


def lsb_bits(value, width):
    return "".join(str((int(value) >> q) & 1) for q in range(width))


def markdown(report):
    records = report["cases"]
    succeeded = sum(r.get("status") == "succeeded" for r in records)
    matched = sum(r.get("consistent") is True for r in records)
    lines = [
        "# OpenQARP examples — OQTOPUS 実行結果", "",
        f"実行開始: {report['started_at']}。接続先: `{report['device_id']}`。",
        f"クラウドジョブ成功: **{succeeded}/{len(records)}**。分布の照合成功: **{matched}/{len(records)}**。", "",
        "## 実行条件", "",
        f"- Python: `{report['python']}` / `{report['platform']}`。uv環境で実行。",
        f"- OpenQARP commit: `{report['openqarp_commit']}`。",
        f"- ライブラリの基準commit（実行時HEAD）: `{report['adapter_commit']}`。本実行用スクリプトはこの作業で追加した未コミットのファイル。",
        f"- 各ジョブ {report['shots']:,} shots、タイムアウト {report['timeout']} 秒。全回路16量子ビット以内。",
        "- `oqtopus-client` と `OpenQARPOqtopusSampler` を使用。",
        "- 7件はsamplingジョブ。QAOAの平均カット重みは取得したbitstringから古典的に集計した指標。",
        "- 対応命令・topology・配置はサーバ側。デバイス向けローカルトランスパイル・ビット反転なし。",
        "- 表のbit列は左から qubit 0（LSB）。整数は Σ bit[i]×2^i。QPEでは測定ancillaの順。",
        "- ノートブックの回路を抽出し、有限shotsのクラウド実行へ接続。描画・表示は省略。",
        "- QAOAの最適化はローカル。クラウドでは最適化後の回路だけを測定。", "",
        "## 再現コマンド", "", "```sh",
        "MPLCONFIGDIR=artifacts/matplotlib uv run --locked --extra qiskit python examples/run_openqarp_examples.py \\",
        f"  --config ~/.oqtopus --device-id {report['device_id']} --shots {report['shots']}",
        "```", "", "## 一覧", "",
        "| ケース | 全量子ビット | 構成回路の命令数 | 結果 | TVD | 許容値 | 呼出秒 |",
        "|---|---:|---:|---|---:|---:|---:|",
    ]
    for record in records:
        tvd = f"{record['tvd']:.6f}" if "tvd" in record else "—"
        threshold = f"{record['threshold']:.6f}" if "threshold" in record else "—"
        state = ("成功・整合" if record.get("consistent") else
                 "成功・不整合" if record.get("status") == "succeeded" else record.get("status", "未実行"))
        lines.append(f"| {case_text(record)[0]} | {record.get('n_qubits', '—')} | {record.get('commands', '—')} | {state} | {tvd} | {threshold} | {record.get('remote_seconds', 0):.2f} |")
    lines.extend([
        "", "命令数は構成したblockのflatten後の数。Samplerが自動追加する測定は含まず、Trotter例で明示追加したReadoutは含む。", "",
        "呼出秒は送受信・サーバ側変換・完了待ち・結果取得を含む壁時計時間。", "",
        "TVD = ½Σ|クラウド確率−参照確率|。解析分布があるケースは解析分布を参照し、",
        "QAOAはローカルの厳密statevector分布と比較する。解析分布とローカル実行の一致も別に確認した。",
        "許容値は多項分布/IID shotsを仮定した保守的な統計境界：",
        "½Σ√(p(1−p)/N) + √(log(1000)/(2N))（ケースごとの超過確率上界0.001）。",
        "この照合は測定確率を対象とし、量子状態の相対位相を単独では検証しない。", "",
    ])
    for record in records:
        title, scope, expectation = case_text(record)
        source_path = record['source'].removeprefix('ref/openqarp/')
        source_url = f"https://github.com/OpenQARP/openqarp/blob/{report['openqarp_commit']}/{source_path}"
        lines.extend([f"## {title}", "",
                      f"出典: [{Path(record['source']).name}]({source_url})、セル {record['cells']}（0始まり）。", "",
                      scope, "", expectation, "",
                      "設定:", "", "```json", json.dumps(record["settings"], indent=2, ensure_ascii=False), "```", ""])
        for job in record.get("jobs", []):
            lines.append(f"- job_id: `{job['job_id']}` / status: `{job['status']}` / shots: {job['n_shots']:,}")
        if "oracle_local_tvd" in record:
            lines.append(f"- 解析分布とローカル厳密分布のTVD: `{record['oracle_local_tvd']:.3e}`")
        for label, value in record.get("metrics", {}).items():
            lines.append(f"- {label}: `{value}`")
        if record.get("server_message"):
            lines.extend(["", "サーバ応答:", "", "```text", record["server_message"], "```"])
        if record.get("error_type"):
            lines.append(f"- エラー種別: `{record['error_type']}`")
        if record.get("probabilities"):
            lines.extend(["", "| 整数 | bit列（LSB先頭） | 参照確率 | クラウド確率 | 回数 |",
                          "|---:|---|---:|---:|---:|"])
            actual = {int(k): v for k, v in record["probabilities"].items()}
            reference = {int(k): v for k, v in record["reference"].items()}
            for outcome in sorted(actual.keys() | {k for k, p in reference.items() if p > 1e-10}):
                p = actual.get(outcome, 0.0)
                lines.append(f"| {outcome} | `{lsb_bits(outcome, len(record['measured_qubits']))}` | {reference.get(outcome, 0):.8f} | {p:.8f} | {round(p * report['shots'])} |")
        lines.append("")
    lines.extend(["## 付随データ", "",
                  f"- 完全な数値結果: `{report['json_path']}`（Git管理外）。",
                  "- 回路のQASMとローカル準備ログ: `artifacts/openqarp-examples/`（Git管理外）。",
                  "- 認証情報・API tokenは出力・コピーしていない。参照ノートブックは変更していない。", ""])
    return "\n".join(lines)


def save(report, output, json_path):
    output.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    output.write_text(markdown(report))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--render", type=Path, help="Render an existing result JSON without execution")
    parser.add_argument("--section", default="default")
    parser.add_argument("--device-id", default="qulacs")
    parser.add_argument("--shots", type=int, default=2000)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/openqarp-examples-results.md")
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--local-only", action="store_true")
    args = parser.parse_args()
    if args.render:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown(json.loads(args.render.read_text())))
        return 0
    if not args.config and not args.local_only:
        parser.error("--config is required for Cloud execution")
    if not 1 <= args.shots <= 10_000:
        parser.error("shots must be between 1 and 10000")
    artifacts = ROOT / "artifacts/openqarp-examples"
    artifacts.mkdir(parents=True, exist_ok=True)
    log_output = io.StringIO()
    with redirect_stdout(log_output):
        cases = algorithm_cases() + state_cases()
    (artifacts / "preparation.log").write_text(log_output.getvalue())
    order = {name: index for index, name in enumerate([
        "grover", "uniform-superposition-11-of-16", "sparse-state-8q",
        "fermi-hubbard-trotter-one-particle", "qrom-superposed-lookup", "qaoa", "qpe",
    ])}
    cases.sort(key=lambda case: order[case["id"]])
    if args.cases:
        unknown = set(args.cases) - {case["id"] for case in cases}
        if unknown:
            parser.error(f"Unknown cases: {sorted(unknown)}")
        cases = [case for case in cases if case["id"] in args.cases]
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "device_id": args.device_id, "shots": args.shots, "timeout": args.timeout,
        "python": platform.python_version(), "platform": platform.platform(),
        "openqarp_commit": subprocess.check_output(["git", "-C", str(ROOT / "ref/openqarp"), "rev-parse", "HEAD"], text=True).strip(),
        "adapter_commit": subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip(),
        "json_path": "artifacts/openqarp-examples/results.json", "cases": [],
    }
    json_path = artifacts / "results.json"
    client = None if args.local_only else OqtopusClient(OqtopusConfig.from_file(args.section, path=args.config))
    for case in cases:
        record = {key: value for key, value in case.items()
                  if key not in ("block", "params", "expected_probabilities", "measured_qubits")}
        report["cases"].append(record)
        sampler = None
        started = time.monotonic()
        try:
            width, commands, measured, local = local_probabilities(case)
            record.update(n_qubits=width, commands=len(commands), measured_qubits=measured)
            oracle = case.get("expected_probabilities")
            if oracle is not None:
                record["oracle_local_tvd"] = total_variation(oracle, local)
                if record["oracle_local_tvd"] > 1e-9:
                    raise ValueError("Independent oracle and local simulation disagree")
            reference = oracle if oracle is not None else local
            record["reference"] = reference
            record["local_reference_seconds"] = time.monotonic() - started
            print(json.dumps({"case": case["id"], "qubits": width, "commands": len(commands), "stage": "local-verified"}), flush=True)
            if args.local_only:
                record["status"] = "local_only"
                continue
            sampler = OpenQARPOqtopusSampler(
                device_id=args.device_id, client=client, n_shots=args.shots, timeout=args.timeout,
            )
            started = time.monotonic()
            distribution = sampler.sample(
                ket=case["block"], params=case["params"], measured_qubits=case["measured_qubits"],
            )
            probabilities = {sum(int(bit) << i for i, bit in enumerate(bits)): float(p)
                             for bits, p in distribution.items()}
            record["probabilities"] = probabilities
            record["tvd"] = total_variation(probabilities, reference)
            record["threshold"] = 0.5 * sum(sqrt(max(0, p * (1 - p)) / args.shots) for p in reference.values()) + sqrt(log(1000) / (2 * args.shots))
            record["consistent"] = record["tvd"] <= record["threshold"]
            record["status"] = "succeeded"
            metrics = {}
            if case["id"] == "qaoa":
                optimal = case["settings"]["optimal_outcomes"]
                metrics["最適カット状態の合計確率"] = sum(probabilities.get(x, 0) for x in optimal)
                edges = case["settings"]["edges"]
                metrics["測定された平均カット重み"] = sum(p * sum(2 for u, v in edges if ((x >> u) & 1) != ((x >> v) & 1)) for x, p in probabilities.items())
            if case["id"] == "qpe":
                peak = max(probabilities, key=probabilities.get)
                metrics["最頻位相bin"] = peak
                metrics["最頻位相bin/32"] = peak / 32
                metrics["理論位相"] = case["settings"]["expected_phase"]
            record["metrics"] = metrics
        except Exception as error:
            record.update(status="failed", error_type=type(error).__name__, consistent=False)
            job_id = getattr(error, "job_id", None)
            if client is not None and job_id:
                try:
                    record["server_message"] = client.get_job_result(job_id).message
                except Exception as retrieval_error:
                    record["message_error_type"] = type(retrieval_error).__name__
        finally:
            if sampler is not None:
                record["remote_seconds"] = time.monotonic() - started
                record["jobs"] = [asdict(job) for job in sampler.jobs]
                try:
                    compiled = sampler.executor._primitives[0].compiled[0]
                    bound = qx.substitute_all(compiled.commands, {str(k): float(v) for k, v in case["params"].items()})
                    program = qx.QASM3Emitter().emit(bound, compiled.n_qubits)
                    (artifacts / f"{case['id']}.qasm").write_text(program)
                except Exception as artifact_error:
                    record["qasm_error_type"] = type(artifact_error).__name__
            save(report, args.output, json_path)
            print(json.dumps({"case": case["id"], "status": record["status"], "tvd": record.get("tvd"), "consistent": record.get("consistent"), "job_ids": [j["job_id"] for j in record.get("jobs", [])]}), flush=True)
    return 0 if args.local_only or all(r.get("consistent") for r in report["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
