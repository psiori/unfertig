"""Bounded complete-source delivery. Source files, never this packet, own authority."""
import argparse
import hashlib
import json
from pathlib import Path
import stat

SOURCE_LIMIT = 32_768
TOTAL_LIMIT = 65_536
SOURCE_COUNT = 48


def read_source(path):
    """Hash the whole source without loading arbitrarily large files into memory."""
    path = Path(path)
    target = path.resolve()
    if not stat.S_ISREG(path.stat().st_mode):
        raise OSError('Context source must be a regular file')
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        prefix = bytearray()
        size = 0
        while chunk := stream.read(65_536):
            digest.update(chunk)
            if size < SOURCE_LIMIT:
                prefix.extend(chunk[:SOURCE_LIMIT-size])
            size += len(chunk)
    return target, size, digest.hexdigest(), bytes(prefix)


def bundle(paths, *, root, developer, role, task):
    entries, remaining = [], TOTAL_LIMIT
    paths = list(dict.fromkeys(str(Path(p).absolute()) for p in paths))
    for path in paths[:SOURCE_COUNT]:
        entry = dict(path=path)
        try:
            target, size, digest, raw = read_source(path)
            entry.update(canonical_path=str(target), bytes=size, sha256=digest)
            if (target, size, digest) != read_source(path)[:3]:
                entry['status'] = 'changed; read current original'
            elif size > min(SOURCE_LIMIT, remaining):
                entry['status'] = 'omitted by size limit; read complete original in bounded chunks'
            else:
                entry.update(status='complete', text=raw.decode('utf-8'))
                remaining -= size
        except UnicodeDecodeError:
            entry['status'] = 'non-UTF8; inspect original'
        except OSError:
            entry['status'] = 'missing; resolve if required'
        entries.append(entry)
    # Recheck the entire set after assembly, not only each individual read.
    for entry in entries:
        if entry.get('status') != 'complete':
            continue
        try:
            target, size, digest, _ = read_source(entry['path'])
            stable = (str(target), size, digest) == (entry['canonical_path'], entry['bytes'], entry['sha256'])
        except OSError:
            stable = False
        if not stable:
            entry.pop('text', None)
            entry['status'] = 'changed during assembly; read current original'
    return dict(builder_version=1, root=str(Path(root).resolve()), developer=developer,
                role=role, task_id=(task or {}).get('id'),
                task_sha256=hashlib.sha256(json.dumps(task or {}, sort_keys=True).encode()).hexdigest(),
                sources=entries, remaining_paths=paths[SOURCE_COUNT:2*SOURCE_COUNT],
                remaining_count=max(0, len(paths)-SOURCE_COUNT),
                complete=not paths[SOURCE_COUNT:] and all(e['status']=='complete' for e in entries))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--sha256', required=True, help='Expected source fingerprint from the packet')
    parser.add_argument('--verify', action='store_true', help='Check freshness without rereading prose into the session')
    parser.add_argument('--offset', type=int, default=0, help='Byte offset for a complete sequence of bounded reads')
    args = parser.parse_args()
    if args.offset < 0:
        parser.error('offset must be nonnegative')
    target, size, digest, _ = read_source(args.path)
    if args.offset > size:
        parser.error('offset exceeds the source size')
    if digest != args.sha256:
        parser.exit(1, 'Source changed; rebuild the packet or read the current original.\n')
    event = dict(path=str(target), sha256=digest, operation='verify' if args.verify else 'read',
                 offset=args.offset, bytes=0, total_bytes=size)
    if not args.verify:
        with target.open('rb') as stream:
            stream.seek(args.offset)
            raw=stream.read(SOURCE_LIMIT)
        if read_source(args.path)[:3] != (target, size, digest):
            parser.exit(1, 'Source changed during read; retry the current original.\n')
        try:
            content = raw.decode('utf-8')
        except UnicodeDecodeError as error:
            if error.reason != 'unexpected end of data' or error.end != len(raw):
                parser.exit(1, 'Source is not UTF8 text or offset is not a character boundary.\n')
            raw = raw[:error.start]
            content = raw.decode('utf-8')
        event.update(bytes=len(raw), next_offset=args.offset+len(raw), complete=args.offset==0 and len(raw)==size)
        event['text'] = content
    print('UNFERTIG_CONTEXT_READ ' + json.dumps(event, ensure_ascii=False))


if __name__ == '__main__':
    main()
