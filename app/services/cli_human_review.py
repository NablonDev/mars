from __future__ import annotations


class CLIHumanReviewPort:
    """Console-based human review used only by the legacy CLI ingest job
    (app/jobs/cli_ingest.py). The API path (app/api/v1) is the primary
    reviewer surface and does not use this class.
    """

    def request_missing_fields(self, payload: dict) -> dict[str, str]:
        cmir = payload["cmir"]
        missing = payload["missing_fields"]

        print("\n--- Human action required: missing mandatory fields ---")
        print(f"Customer : {cmir.get('customer_identity') or '(unknown)'}")
        print(f"Material : {cmir.get('material_identity') or '(unknown)'}")

        answers: dict[str, str] = {}
        for field_name in missing:
            answers[field_name] = input(f"Enter value for '{field_name}': ").strip()

        return answers

    def request_approval(self, payload: dict) -> dict[str, str]:
        cmir = payload["cmir"]

        print("\n--- Review CMIR before writing to the database ---")
        for key, value in cmir.items():
            if key in ("status", "missing_fields"):
                continue
            print(f"  {key}: {value}")

        while True:
            choice = input("Approve and insert into DB? [y/n]: ").strip().lower()
            if choice in ("y", "yes"):
                return {"decision": "approve", "reason": ""}
            if choice in ("n", "no"):
                reason = input("Reason for rejection: ").strip()
                return {"decision": "reject", "reason": reason}
            print("Please answer 'y' or 'n'.")
