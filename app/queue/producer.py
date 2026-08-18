from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from app.core.config import ServiceBusConfig


class ServiceBusMailQueue:
    """Azure Service Bus adapter for the CMIR mail-processing queue."""

    def __init__(self, config: ServiceBusConfig, credential: Any | None = None) -> None:
        self._config = config
        self._credential = credential

    def send(self, payload: dict[str, Any], *, message_id: str) -> None:
        self.send_many([(payload, message_id)])

    def send_many(self, messages: Iterable[tuple[dict[str, Any], str]]) -> None:
        from azure.servicebus import ServiceBusMessage

        with self._create_client() as client:
            with client.get_queue_sender(self._config.queue_name) as sender:
                for payload, message_id in messages:
                    message = ServiceBusMessage(
                        json.dumps(payload),
                        message_id=message_id,
                        content_type="application/json",
                    )
                    message.session_id = self._config.session_id
                    sender.send_messages(message)

    def verify_connection(self) -> None:
        with self._create_client() as client, client.get_queue_sender(self._config.queue_name):
            return

    def _create_client(self):
        from azure.servicebus import ServiceBusClient

        # Development - Use Connection String
        if getattr(self._config, "connection_string", None):
            return ServiceBusClient.from_connection_string(conn_str=self._config.connection_string)

        # Production - Use Managed Identity / Azure AD
        credential = self._credential or self._default_credential()
        return ServiceBusClient(
            fully_qualified_namespace=self._config.fully_qualified_namespace,
            credential=credential,
        )

    @staticmethod
    def _default_credential() -> Any:
        from azure.identity import DefaultAzureCredential

        return DefaultAzureCredential()
