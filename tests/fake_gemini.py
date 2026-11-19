"""ตัวจำลอง google.genai.Client สำหรับทดสอบ — ไม่เรียก API จริง"""
import hashlib
from types import SimpleNamespace

DIM = 768


def hash_vec(text):
    """เวกเตอร์คงที่จากข้อความ (ข้อความเดียวกัน = เวกเตอร์เดียวกัน)"""
    v = [0.0] * DIM
    for word in (text or "").split():
        h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
        v[h % DIM] += 1.0
    if not any(v):
        v[0] = 1.0
    return v


class _Models:
    def __init__(self, fake):
        self.fake = fake

    def embed_content(self, model, contents, config=None):
        self.fake.calls.append(("embed", model, contents))
        if self.fake.fail or self.fake.fail_embed:
            raise RuntimeError("embed quota")
        vec = self.fake.embed_fn(contents) if self.fake.embed_fn else hash_vec(contents)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=list(vec))])

    def generate_content(self, model, contents, config=None):
        self.fake.calls.append(("generate", model, contents))
        if self.fake.on_generate:
            self.fake.on_generate()
        if self.fake.fail:
            raise RuntimeError("gemini down")
        return SimpleNamespace(text=self.fake.reply)


class FakeGemini:
    def __init__(self, reply="", embed_fn=None, fail=False, fail_embed=False):
        self.fail_embed = fail_embed
        self.on_generate = None
        self.reply = reply
        self.embed_fn = embed_fn
        self.fail = fail
        self.calls = []
        self.models = _Models(self)

    def count(self, kind):
        return sum(1 for c in self.calls if c[0] == kind)
