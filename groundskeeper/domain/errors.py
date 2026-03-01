"""Domain errors for Groundskeeper."""


class SkillNotFoundError(Exception):
    """Raised when a skill cannot be found in any store."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Skill not found: {name}")


class SkillValidationError(Exception):
    """Raised when a skill file has invalid frontmatter or structure."""

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"Invalid skill at {path}: {message}")
