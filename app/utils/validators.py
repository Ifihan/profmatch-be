def ensure_protocol(url: str, default_protocol: str = "https://") -> str:
    """
    Ensure the URL has a protocol (http:// or https://).
    If missing, prepends the default protocol.
    """
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        return f"{default_protocol}{url}"
    return url