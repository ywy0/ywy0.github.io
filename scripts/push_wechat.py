#!/usr/bin/env python3
"""
Push GitHub blog posts (Markdown) to WeChat Official Account as drafts.

Usage:
    # Single post
    python scripts/push_wechat.py --post _posts/2024-01-01-example.md

    # All unsynced posts
    python scripts/push_wechat.py --all

Requires: pip install requests markdown
"""

import argparse, hashlib, json, os, re, sys
from datetime import datetime
from pathlib import Path

import requests

# ============================================================
#  Config
# ============================================================
STATE_FILE = '.wechat-push-state.json'   # tracks which posts are already synced
WECHAT_API = 'https://api.weixin.qq.com/cgi-bin'


# ============================================================
#  WeChat API helpers
# ============================================================

class WeChatAPI:
    def __init__(self, appid: str, secret: str):
        self.appid = appid
        self.secret = secret
        self.token = None

    def _refresh_token(self):
        resp = requests.get(f'{WECHAT_API}/token', params={
            'grant_type': 'client_credential',
            'appid': self.appid,
            'secret': self.secret,
        }).json()
        if 'access_token' not in resp:
            raise RuntimeError(f"Token error: {resp}")
        self.token = resp['access_token']

    def _ensure_token(self):
        if not self.token:
            self._refresh_token()

    def upload_article_image(self, filepath: str) -> str | None:
        """Upload an image for use in article body. Returns WeChat URL."""
        self._ensure_token()
        path = Path(filepath)
        if not path.exists():
            return None
        try:
            with open(path, 'rb') as f:
                file_data = f.read()
            files = {'media': (path.name, file_data, self._mime_type(path))}
            resp = requests.post(
                f'{WECHAT_API}/media/uploadimg',
                params={'access_token': self.token},
                files=files, timeout=30,
            ).json()
            if 'url' in resp:
                return resp['url']
            # Token expired, retry once
            if resp.get('errcode') == 40001:
                self._refresh_token()
                with open(path, 'rb') as f:
                    file_data = f.read()
                files = {'media': (path.name, file_data, self._mime_type(path))}
                resp = requests.post(
                    f'{WECHAT_API}/media/uploadimg',
                    params={'access_token': self.token},
                    files=files, timeout=30,
                ).json()
                if 'url' in resp:
                    return resp['url']
            print(f"    [!] Upload failed: {resp.get('errcode', '?')} {resp.get('errmsg', '?')}")
            return None
        except Exception as e:
            print(f"    [!] Upload error: {e}")
            return None

    def _mime_type(self, path: Path) -> str:
        ext = path.suffix.lower()
        return {
            '.png': 'image/png', '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg', '.gif': 'image/gif',
            '.webp': 'image/webp',
        }.get(ext, 'image/png')

    def upload_cover(self, filepath: str) -> str | None:
        """Upload image as permanent material (for article cover). Returns media_id."""
        self._ensure_token()
        path = Path(filepath)
        if not path.exists():
            return None
        try:
            with open(path, 'rb') as f:
                file_data = f.read()
            files = {'media': (path.name, file_data, self._mime_type(path))}
            resp = requests.post(
                f'{WECHAT_API}/material/add_material',
                params={'access_token': self.token, 'type': 'image'},
                files=files, timeout=30,
            ).json()
            if 'media_id' in resp:
                return resp['media_id']
            if resp.get('errcode') == 40001:
                self._refresh_token()
                with open(path, 'rb') as f:
                    file_data = f.read()
                resp = requests.post(
                    f'{WECHAT_API}/material/add_material',
                    params={'access_token': self.token, 'type': 'image'},
                    files=files, timeout=30,
                ).json()
                if 'media_id' in resp:
                    return resp['media_id']
            # Log the full response for debugging
            safe = str(resp.get('errmsg', '?')).encode('ascii', errors='replace').decode('ascii')
            print(f"    [!] Cover upload failed: errcode={resp.get('errcode', '?')} {safe}")
            return None
        except Exception as e:
            print(f"    [!] Cover upload error: {e}")
            return None

    def create_draft(self, title: str, html_content: str,
                     thumb_media_id: str, author: str = '') -> dict:
        """Create a draft in the WeChat draft box."""
        self._ensure_token()
        payload = {
            'articles': [{
                'title': title[:32],
                'author': author[:16] if author else '',
                'content': html_content,
                'thumb_media_id': thumb_media_id,
                'need_open_comment': 0,
                'only_fans_can_comment': 0,
            }]
        }
        # Ensure Chinese chars sent as UTF-8, not \\uXXXX escapes
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json; charset=utf-8'}
        resp = requests.post(
            f'{WECHAT_API}/draft/add',
            params={'access_token': self.token},
            data=body,
            headers=headers,
            timeout=30,
        ).json()
        if resp.get('errcode') == 40001:
            self._refresh_token()
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            resp = requests.post(
                f'{WECHAT_API}/draft/add',
                params={'access_token': self.token},
                data=body,
                headers=headers,
                timeout=30,
            ).json()
        return resp


# ============================================================
#  Markdown processing
# ============================================================

def parse_front_matter(content: str) -> tuple[dict, str]:
    """Parse Jekyll front matter (YAML-like) and return (front_dict, body)."""
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {}, content.strip()

    raw = parts[1]
    front = {}
    current_key = None
    in_list = False

    for line in raw.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Tag list entries: "- tag"
        if line_stripped.startswith('- '):
            val = line_stripped[2:].strip()
            if current_key:
                if not isinstance(front.get(current_key), list):
                    front[current_key] = []
                front[current_key].append(val)
            continue

        # Key: value
        if ':' in line and not line_stripped.startswith('#'):
            colon_pos = line.index(':')
            key = line[:colon_pos].strip()
            val = line[colon_pos + 1:].strip().strip('"').strip("'")
            front[key] = val
            current_key = key

    return front, parts[2].strip()


def guess_content_type(md_body: str) -> str:
    """Try to determine if this is code-heavy or prose-heavy."""
    code_blocks = len(re.findall(r'```', md_body)) // 2
    inline_code = len(re.findall(r'`[^`]+`', md_body))
    return 'code' if code_blocks > 0 or inline_code > 5 else 'prose'


def wechat_safe_html(html: str) -> str:
    """Convert HTML to be compatible with WeChat article editor.

    WeChat strips <table>, <hr>, and some other tags. Convert them
    to WeChat-friendly alternatives.
    """
    import re

    # Convert <table> to formatted text (WeChat doesn't support tables)
    def table_to_text(m):
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(0), re.DOTALL)
        lines = []
        for row in rows:
            cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)
            # Strip HTML tags inside cells for plain text
            text_cells = []
            for c in cells:
                text = re.sub(r'<[^>]+>', '', c).strip()
                text_cells.append(text)
            lines.append(' | '.join(text_cells))
        # Add separator line after header
        if len(lines) > 1:
            sep = ' | '.join(['---'] * len(lines[0].split(' | ')))
            lines.insert(1, sep)
        return '<p>' + '<br>\n'.join(lines) + '</p>'

    html = re.sub(r'<table[^>]*>.*?</table>', table_to_text, html, flags=re.DOTALL)

    # Convert <hr> to a text separator (WeChat strips <hr>)
    html = html.replace('<hr />', '<p>━━━━━━━━━━━━━━━━</p>')
    html = html.replace('<hr>', '<p>━━━━━━━━━━━━━━━━</p>')

    return html


def md_to_html(md_body: str) -> str:
    """Convert Markdown to WeChat-compatible HTML."""
    import markdown as md_lib
    html = md_lib.markdown(md_body, extensions=[
        'markdown.extensions.extra',
        'markdown.extensions.codehilite',
        'markdown.extensions.toc',
        'markdown.extensions.sane_lists',
    ])
    return html


def extract_images(md_body: str, repo_root: Path) -> list[tuple[str, str, str, str]]:
    """
    Find all images in markdown.
    Returns list of (original_full_ref, alt_text, clean_path, local_filepath).
    """
    images = []
    # Markdown syntax: ![alt](path)
    for m in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', md_body):
        alt, raw_path = m.group(1), m.group(2)
        # Strip optional title: `path "title"` → `path`
        raw_path = raw_path.split('"')[0].split("'")[0].strip()
        clean_path = re.sub(r'\{\{[^}]+\}\}', '', raw_path).strip()
        images.append(('md', alt, raw_path, clean_path))

    # HTML syntax: <img src="path">
    for m in re.finditer(r'<img\s+[^>]*src=["\']([^"\']+)["\']', md_body):
        raw_path = m.group(1)
        clean_path = re.sub(r'\{\{[^}]+\}\}', '', raw_path).strip()
        images.append(('html', '', raw_path, clean_path))

    return images


def resolve_image_path(original_path: str, repo_root: Path) -> str | None:
    """Resolve an image reference to a local file path."""
    p = original_path
    if p.startswith('/'):
        p = p[1:]

    candidates = [
        repo_root / p,
        repo_root / 'file' / Path(p).name,
        repo_root / 'assets' / p,
        repo_root / 'images' / p,
    ]

    # Also try the raw path under various dirs
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    # If the path has directory components, try relative to repo root
    full = (repo_root / p).resolve()
    if full.exists():
        return str(full)

    return None


# ============================================================
#  Main
# ============================================================

def process_post(post_path_str: str, repo_root: Path,
                 wx: WeChatAPI, author: str = '') -> bool:
    """Process one markdown post: upload images, create WeChat draft."""
    post_path = Path(post_path_str).resolve()
    if not post_path.exists():
        print(f"  [!] Not found: {post_path}")
        return False

    content = post_path.read_text(encoding='utf-8')
    content_hash = hashlib.md5(content.encode()).hexdigest()

    # Check state
    state_path = repo_root / STATE_FILE
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding='utf-8'))

    rel_path = str(post_path.relative_to(repo_root))
    if state.get(rel_path) == content_hash:
        print(f"  > Already synced (unchanged): {rel_path}")
        return True

    # Parse front matter
    front, md_body = parse_front_matter(content)
    title = front.get('title', post_path.stem)
    safe_title = title.encode('ascii', errors='replace').decode('ascii')
    print(f"\n  [P] {safe_title}")
    print(f"     Path: {rel_path}")

    # Extract & upload images
    images = extract_images(md_body, repo_root)
    print(f"     Images found: {len(images)}")

    img_map = {}  # original_md_ref -> WeChat URL
    for img_type, alt, raw_ref, clean_path in images:
        local = resolve_image_path(clean_path, repo_root)
        if not local:
            print(f"     [!] Image not found: {clean_path[:60]}")
            continue
        fname = Path(clean_path).name
        print(f"     ^ Uploading: {fname}...")
        url = wx.upload_article_image(local)
        if url:
            img_map[raw_ref] = url

    # Replace image references in markdown BEFORE converting to HTML
    # Sort by length (longest first) to avoid partial replacements
    for old in sorted(img_map.keys(), key=len, reverse=True):
        new_url = img_map[old]
        # Markdown syntax
        md_body = md_body.replace(f']({old})', f']({new_url})')
        md_body = md_body.replace(f']({old} ', f']({new_url} ')
        # HTML syntax
        md_body = md_body.replace(f'src="{old}"', f'src="{new_url}"')
        md_body = md_body.replace(f"src='{old}'", f"src='{new_url}'")

    # Convert to HTML + make WeChat-compatible
    html_body = md_to_html(md_body)
    html_body = wechat_safe_html(html_body)

    # Upload cover image
    cover_media_id = None
    if images:
        first_img_path = resolve_image_path(images[0][3], repo_root)
        if first_img_path:
            print(f"     ^ Uploading cover...")
            cover_media_id = wx.upload_cover(first_img_path)

    if not cover_media_id:
        # No image in post - generate a simple default cover
        print(f"     ^ Generating default cover...")
        import base64, struct, zlib

        def make_png(w, h, rgb):
            """Create a minimal PNG with a solid color."""
            raw = b''
            for y in range(h):
                raw += b'\x00'  # filter byte
                for x in range(w):
                    raw += struct.pack('BBB', *rgb)
            compressed = zlib.compress(raw)
            ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
            def chunk(ctype, data):
                c = ctype + data
                return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
            return (b'\x89PNG\r\n\x1a\n'
                    + chunk(b'IHDR', ihdr)
                    + chunk(b'IDAT', compressed)
                    + chunk(b'IEND', b''))

        cover_png = make_png(900, 383, (96, 125, 139))  # 900x383 blue-grey (符合微信封面比例)
        cover_path = repo_root / '.cover_tmp.png'
        cover_path.write_bytes(cover_png)
        cover_media_id = wx.upload_cover(str(cover_path))
        cover_path.unlink(missing_ok=True)

    if not cover_media_id:
        print(f"     [X] Cannot create draft without cover image")
        return False

    # Create draft
    print(f"     -> Creating draft...")
    result = wx.create_draft(title, html_body, cover_media_id, author)

    if 'media_id' in result:
        print(f"     [OK] Draft created! media_id: {result['media_id']}")
        # Update state
        state[rel_path] = content_hash
        state_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        return True
    else:
        err_msg = str(result.get('errmsg', '?'))
        safe_err = err_msg.encode('ascii', errors='replace').decode('ascii')
        print(f"     [X] Failed: errcode={result.get('errcode', '?')} {safe_err}")
        # Log raw response for debugging on Windows
        with open(repo_root / '.wechat-debug.log', 'a', encoding='utf-8') as df:
            df.write(f"[{title}] {json.dumps(result, ensure_ascii=False)}\n")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Push GitHub posts to WeChat draft box'
    )
    parser.add_argument('--post', help='Single post file to process')
    parser.add_argument('--all', action='store_true',
                        help='Process all unsynced posts')
    parser.add_argument('--appid', required=True,
                        help='WeChat AppID')
    parser.add_argument('--secret', required=True,
                        help='WeChat AppSecret')
    parser.add_argument('--author', default='忆屋',
                        help='Author name (max 16 chars)')
    args = parser.parse_args()

    # Repo root = where .git is
    repo_root = Path.cwd().resolve()

    wx = WeChatAPI(args.appid, args.secret)

    # Collect posts to process
    if args.post:
        posts = [args.post]
    elif args.all:
        posts_dir = repo_root / '_posts'
        if not posts_dir.exists():
            print(f"[ERR] _posts/ directory not found in {repo_root}")
            sys.exit(1)
        posts = sorted(posts_dir.glob('*.md'))
        if not posts:
            print("[i]  No posts found in _posts/")
            return
    else:
        parser.print_help()
        sys.exit(1)

    print(f"[OUT] Pushing to WeChat draft box ({len(posts)} post(s))...")

    ok = 0
    for p in posts:
        if process_post(str(p), repo_root, wx, args.author):
            ok += 1

    print(f"\n{'='*40}")
    print(f"Done: {ok}/{len(posts)} posts pushed to WeChat drafts")
    print(f"[NOTE] Go to mp.weixin.qq.com -> 草稿箱 to review and publish")
    print(f"{'='*40}")

    # GitHub Actions output
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f"pushed={ok}\n")


if __name__ == '__main__':
    main()
