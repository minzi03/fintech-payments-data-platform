"""Safe deterministic configuration diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

from portal_api.configuration.models import PortalProcessRole


@dataclass(frozen=True)
class ConfigurationDiagnostic:
    code: str
    profile: str
    process_role: PortalProcessRole | None
    field_path: str
    source: str
    safe_reason: str

    def render(self) -> str:
        role = self.process_role.value if self.process_role is not None else "unassigned"
        return (
            f"{self.code}: profile={self.profile} role={role} "
            f"field={self.field_path} source={self.source} reason={self.safe_reason}"
        )


class PortalConfigurationError(ValueError):
    """Configuration failure whose message is safe for startup diagnostics."""

    def __init__(self, diagnostics: tuple[ConfigurationDiagnostic, ...]) -> None:
        ordered = tuple(
            sorted(
                diagnostics,
                key=lambda item: (
                    item.code,
                    item.field_path,
                    item.source,
                    item.safe_reason,
                ),
            )
        )
        self.diagnostics = ordered
        super().__init__("; ".join(item.render() for item in ordered))


class PortalConfigurationWarning(UserWarning):
    """Safe local/development compatibility warning."""
