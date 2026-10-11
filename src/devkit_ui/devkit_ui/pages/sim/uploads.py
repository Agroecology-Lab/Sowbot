async def read_upload(event, default_name: str) -> tuple[bytes, str]:
    """Return an uploaded file's bytes and name.

    NiceGUI changed the upload event shape across versions: newer ones expose event.file (a
    FileUpload with an async read()), older ones event.content (a synchronous file-like object).
    """
    if hasattr(event, 'file'):
        data = await event.file.read()
        name = event.file.name if hasattr(event.file, 'name') else default_name
    else:
        data = event.content.read()
        name = getattr(event, 'name', default_name)
    return data, name
