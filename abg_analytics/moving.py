import errno
import os
import shutil
from pathlib import Path

from .inventory import fingerprint, linked, path_key, within


def check_plain_path(path):
    """Refuse links/junctions in an operation's existing ancestor chain."""
    for part in (path, *path.parents):
        if linked(part):
            raise ValueError(f"Linked files and directories cannot be moved through: {part}")


def validate_trash_directory(value, roots):
    if not isinstance(value, str):
        raise ValueError("Trash directory must be a path string.")
    if not value.strip():
        return ""
    directory = Path(value.strip()).expanduser()
    if not directory.is_absolute():
        raise ValueError("Choose an absolute directory on the computer running ComfyUI.")
    # The explicitly selected parent may be an alias (e.g. macOS /var or a
    # Windows drive junction). Store its canonical target as the trust boundary.
    directory = directory.resolve()
    check_plain_path(directory)
    trash = directory / "trash"
    check_plain_path(trash)
    if directory.exists() and not directory.is_dir():
        raise ValueError("Trash directory must be a folder, not a file.")
    for item in roots:
        root = Path(item["path"]).resolve()
        if within(trash, root) or within(root, trash):
            raise ValueError("Trash must be outside every configured model directory.")
    return str(directory)


def unique_destination(trash, logical_path, model_id, reserved):
    destination = trash / logical_path
    # Keep the requested hierarchy; use a separate source bucket on collision.
    index = 0
    while os.path.lexists(destination) or path_key(destination).casefold() in reserved:
        index += 1
        destination = trash / "collisions" / f"{model_id[:12]}-{index}" / logical_path
    check_plain_path(destination)
    if not within(destination.resolve(), trash.resolve()):
        raise ValueError("Destination escapes the trash directory.")
    reserved.add(path_key(destination).casefold())
    return destination


def move_no_replace(source, destination, expected):
    """Never overwrite; unlink the source only after a complete, durable copy."""
    source, destination = Path(source), Path(destination)
    check_plain_path(source)
    check_plain_path(destination)
    if not source.is_file() or fingerprint(source) != expected:
        raise ValueError("The source file changed; refresh and preview it again.")
    if os.path.lexists(destination):
        raise FileExistsError(f"Destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    check_plain_path(destination)
    created = False
    try:
        # Hard-link + unlink is fast on the same volume and cannot overwrite.
        try:
            os.link(source, destination)
            created = True
        except OSError as error:
            if error.errno not in {errno.EXDEV, errno.EPERM, errno.EACCES, errno.ENOTSUP, errno.EINVAL, errno.ENOSYS}:
                raise
            with source.open("rb") as reader:
                if fingerprint(source) != expected:
                    raise ValueError("The source file changed before copying.")
                with destination.open("xb") as writer:
                    created = True
                    shutil.copyfileobj(reader, writer, length=8 * 1024 * 1024)
                    writer.flush()
                    os.fsync(writer.fileno())
            shutil.copystat(source, destination)
        if fingerprint(source) != expected or destination.stat().st_size != expected[2]:
            raise ValueError("The source changed or the copy was incomplete; source kept.")
        source.unlink()
    except BaseException:
        if created and source.exists():
            try:
                destination.unlink()
            except OSError:
                pass  # Journal remains available for manual recovery.
        raise
