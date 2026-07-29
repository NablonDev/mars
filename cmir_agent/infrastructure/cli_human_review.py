from __future__ import annotations

from typing import Dict

from cmir_agent.interfaces.human_review import HumanReviewPort


class CLIHumanReviewPort(HumanReviewPort):
    """Console implementation of the human-review port.

    Replace this one class with a web/API-backed implementation later
    (e.g. one that reads the answer from a "Workbench" HTTP request) and
    nothing in domain/ or workflow/ needs to change.
    """

    def request_missing_fields(self, payload: Dict) -> Dict[str, str]:
        cmir = payload["cmir"]
        missing = payload["missing_fields"]

        print("\n--- Human action required: missing mandatory fields ---")
        print(f"Customer : {cmir.get('customer_identity') or '(unknown)'}")
        print(f"Material : {cmir.get('material_identity') or '(unknown)'}")

        answers: Dict[str, str] = {}
        for field_name in missing:
            answers[field_name] = input(f"Enter value for '{field_name}': ").strip()

        return answers

    def request_approval(self, payload: Dict) -> Dict[str, str]:
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
