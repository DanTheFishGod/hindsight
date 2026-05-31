"""Content Index API entry / icon decoder.

Sources:
  - Schema: content/browser/content_index/content_index.proto
  - Writer: content/browser/content_index/content_index_database.cc

The Content Index API lets a PWA register pre-cached content (articles,
podcasts, etc.) the user can browse from the Downloads / Content surface.
Each entry produces two LDB records per registration:

  content_index:entry_<id>  → ContentEntry (id, title, description, category,
                              launch URL, icon definitions, creation timestamp)
  content_index:icon_<id>   → SerializedIcons (PNG bytes for each declared icon)

The icon record's value is just the PNG bytes (potentially large), so we
surface byte count + a count of stored icons rather than dumping the raw bytes.
"""

from pyhindsight import utils
from pyhindsight.lib.proto.content.browser.content_index.content_index_pb2 import (
    ContentEntry, SerializedIcons)


ENTRY_PREFIX = 'content_index:entry_'
ICON_PREFIX = 'content_index:icon_'


def matches(user_data_key):
    return (user_data_key.startswith(ENTRY_PREFIX)
            or user_data_key.startswith(ICON_PREFIX))


def decode(user_data_key, value, timezone):
    if user_data_key.startswith(ICON_PREFIX):
        icons = SerializedIcons()
        try:
            icons.ParseFromString(value)
        except Exception as e:
            return ('content index (icon)',
                    f'<SerializedIcons decode failed: {e}>', None)
        sizes = [len(i.icon) for i in icons.icons if i.HasField('icon')]
        return ('content index (icon)',
                f'icon_count={len(sizes)}; total_bytes={sum(sizes)}; '
                f'per_icon_bytes={sizes}',
                None)

    entry = ContentEntry()
    try:
        entry.ParseFromString(value)
    except Exception as e:
        return ('content index (entry)',
                f'<ContentEntry decode failed: {e}>', None)

    pieces = []
    desc = entry.description
    if desc.HasField('id'):
        pieces.append(f'id={desc.id!r}')
    if desc.HasField('title'):
        pieces.append(f'title={desc.title!r}')
    if desc.HasField('description') and desc.description:
        d = desc.description
        d = d if len(d) <= 160 else d[:160] + '…'
        pieces.append(f'description={d!r}')
    if desc.HasField('category'):
        pieces.append(f'category={desc.category}')
    if desc.HasField('launch_url'):
        pieces.append(f'declared_launch_url={desc.launch_url}')
    if entry.HasField('launch_url') and entry.launch_url:
        pieces.append(f'resolved_launch_url={entry.launch_url}')
    if desc.icons:
        icon_strs = []
        for i in desc.icons:
            bits = []
            if i.HasField('src'):
                bits.append(f'src={i.src}')
            if i.HasField('sizes') and i.sizes:
                bits.append(f'sizes={i.sizes}')
            if i.HasField('type') and i.type:
                bits.append(f'type={i.type}')
            icon_strs.append('{' + ', '.join(bits) + '}')
        pieces.append(f'icon_defs=[{", ".join(icon_strs)}]')
    if entry.HasField('is_top_level_context'):
        pieces.append(f'is_top_level_context={entry.is_top_level_context}')

    # timestamp is "from Windows epoch in microseconds" per the proto comment.
    event_time = None
    if entry.HasField('timestamp') and entry.timestamp:
        event_time = utils.to_datetime(entry.timestamp, timezone)

    return ('content index (entry)', '; '.join(pieces), event_time)
