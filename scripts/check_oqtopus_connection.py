"""Read device metadata with oqtopus-client, without submitting a job."""

import argparse
import json

from oqtopus_client import OqtopusClient, OqtopusConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="INI file path; omitted uses SDK default")
    parser.add_argument("--section", default="default")
    parser.add_argument("--device-id", required=True)
    args = parser.parse_args()
    try:
        config = (
            OqtopusConfig.from_file(args.section, path=args.config)
            if args.config
            else OqtopusConfig.from_file(args.section)
        )
        client = OqtopusClient(config)
        device = client.get_device(args.device_id)
        report = {
            "device_id": device.device_id,
            "device_type": device.device_type,
            "status": device.status,
            "n_qubits": device.n_qubits,
            "basis_gates": device.basis_gates,
            "supported_instructions": device.supported_instructions,
            "job_submitted": False,
        }
    except Exception as error:
        # SDK errors may contain response bodies or connection details.
        print(json.dumps({"connection_ok": False, "error_type": type(error).__name__}))
        return 1
    print(json.dumps({"connection_ok": True, **report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
