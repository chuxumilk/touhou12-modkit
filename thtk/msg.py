# -*- coding: utf-8 -*-
"""TH12 对话文件 (.msg) 解析 —— 对应 thtk 的 ``thmsg/thmsg06.c``。

TH10 以后（含 TH12）的 .msg 沿用 TH06 的**指令流**结构：

    文件头:
        u32 entry_count
        条目表: entry_count * (i32 offset, i32 extra)
                offset = 该条目首指令在文件中的偏移
                extra  = 条目标识（原样保留，TH12 里通常是 0x100）
    指令流（紧跟条目表之后，直到文件尾）:
        u16 time
        u8  type
        u8  length
        u8  data[length]

条目分隔：``time == 0 且 type == 0`` 的零长指令即"条目开始"标记，
其文件偏移正好等于条目表里的 offset。

文本：``type == 17`` 的指令 data 是一段 **XOR 混淆** 的字符串
（滚动密钥 key=0x77, step1=+7, step2=+16），解码后是 Shift-JIS
（汉化版是 GBK）。字符串按 4 字节对齐补零。
"""

import os
import re
import struct

#: 文本指令的 type
TEXT_TYPE = 17
#: 文本 XOR 参数（thtk: util_xor(data, len, 0x77, 7, 16)）
TEXT_XOR_KEY = 0x77
TEXT_XOR_STEP1 = 7
TEXT_XOR_STEP2 = 16

#: 说话人切换指令（对应 th11/th12 的 msg 指令表）
SPEAKER_PLAYER = 7     # 自机说话
SPEAKER_BOSS = 8       # 敌机（Boss）说话
SPEAKER_NONE = 9       # 旁白（无立绘）
#: 立绘表情指令
FACE_PLAYER = 13
FACE_BOSS = 14

#: 指令名称表（th11 地图 + th12 新增，用于界面提示）
INSTR_NAMES = {
    0: "end", 1: "playerShow", 2: "bossShow", 3: "textboxShow",
    4: "playerHide", 5: "bossHide", 6: "textboxHide",
    7: "speakerPlayer", 8: "speakerBoss", 9: "speakerNone",
    10: "skippable", 11: "textPause", 12: "eclResume",
    13: "playerFace", 14: "bossFace", 15: "textLine1", 16: "textLine2",
    17: "textAdd", 18: "textClear", 19: "musicBoss", 20: "intro",
    21: "stageEnd", 22: "musicEnd", 23: "playerShake", 24: "bossShake",
    25: "textOffsetY", 26: "flag2", 27: "musicFade", 28: "bubblePos",
    29: "bubbleType", 30: "routeSelect", 33: "portraitDarken",
    34: "portraitHighlight", 35: "lightsOut",
}

#: 说话人显示名（没有具体角色名时的兜底）
SPEAKER_LABELS = {
    "player": "自机",
    "boss": "敌机",
    "none": "旁白",
}

#: 可玩角色（对话文件名的中间两位数字）
PLAYER_CHARS = {
    "00": "博丽灵梦",
    "01": "雾雨魔理沙",
    "02": "东风谷早苗",
}

#: 关卡 Boss（用于把「敌机」显示成具体名字）
STAGE_BOSS = {
    "st01": "娜兹琳",
    "st02": "多多良小伞",
    "st03": "云居一轮",
    "st04": "村纱水蜜",
    "st05": "寅丸星",
    "st06": "圣白莲",
    "st07": "封兽鵺",
}


class MsgError(Exception):
    """对话文件结构不正确。"""


def describe_file(filename):
    """从对话文件名推断 角色 / 关卡 / 场景。

    ``st03_00a.msg`` -> 关卡 st03、角色 博丽灵梦、场景 中Boss战
    ``st03_01b.msg`` -> 关卡 st03、角色 雾雨魔理沙、场景 Boss战
    """
    info = {"stage": None, "boss": None, "player": None, "scene": None}
    base = os.path.basename(filename)
    if "." in base:
        base = base.rsplit(".", 1)[0]
    base = base.lower()
    m = re.match(r"^(st\d\d|ex\d\d?)_(\d\d)([ab])$", base)
    if not m:
        if re.match(r"^e\d\d$", base):
            info["scene"] = "结局对话"
        elif base.startswith("staff"):
            info["scene"] = "制作人员"
        return info
    stage, char_id, scene = m.group(1), m.group(2), m.group(3)
    info["stage"] = stage
    info["boss"] = STAGE_BOSS.get(stage)
    info["player"] = PLAYER_CHARS.get(char_id)
    info["scene"] = "中Boss战" if scene == "a" else "Boss战"
    return info


def check_encoding(text, encoding):
    """返回无法用 ``encoding`` 表示的字符列表。"""
    bad = []
    for ch in text:
        try:
            ch.encode(encoding)
        except UnicodeEncodeError:
            bad.append(ch)
    return bad


def speaker_labels(filename):
    """返回该文件里三种说话人的显示名。

    自机 / 敌机 会尽量换成具体角色名（如 ``博丽灵梦`` / ``云居一轮``）。
    """
    info = describe_file(filename)
    return {
        "player": info.get("player") or SPEAKER_LABELS["player"],
        "boss": info.get("boss") or SPEAKER_LABELS["boss"],
        "none": SPEAKER_LABELS["none"],
    }


#: 文档里允许出现的说话人标签（导入时用于识别并剥离）
def _label_set():
    labels = set(SPEAKER_LABELS.values())
    labels.update(PLAYER_CHARS.values())
    labels.update(STAGE_BOSS.values())
    return labels


def rolling_xor(data, key=TEXT_XOR_KEY, step1=TEXT_XOR_STEP1,
                step2=TEXT_XOR_STEP2):
    """ZUN 滚动密钥 XOR（自逆），对应 thtk 的 ``util_xor``。"""
    out = bytearray(data)
    key &= 0xFF
    step1 &= 0xFF
    step2 &= 0xFF
    for i in range(len(out)):
        out[i] ^= key
        key = (key + step1) & 0xFF
        step1 = (step1 + step2) & 0xFF
    return bytes(out)


class Instruction(object):
    """一条 .msg 指令。"""

    __slots__ = ("time", "type", "data")

    def __init__(self, time, type_, data):
        self.time = time
        self.type = type_
        self.data = data

    @property
    def is_entry_marker(self):
        """条目开始标记（time=0, type=0, 空 data）。"""
        return self.time == 0 and self.type == 0 and not self.data

    @property
    def is_text(self):
        return self.type == TEXT_TYPE

    def text(self, encoding="cp932"):
        """解码文本指令；非文本指令返回 None。"""
        if not self.is_text:
            return None
        raw = rolling_xor(self.data)
        raw = raw.split(b"\0", 1)[0]
        return raw.decode(encoding, "replace")

    def set_text(self, text, encoding="cp932"):
        """写入文本指令（自动补零到 4 字节对齐，与 thtk 写回逻辑一致）。"""
        if not self.is_text:
            raise MsgError("不是文本指令")
        raw = text.encode(encoding, "replace")
        raw += b"\0" * (4 - (len(raw) % 4))
        self.data = rolling_xor(raw)

    def __repr__(self):
        return "<Instr t=%d type=%d len=%d>" % (
            self.time, self.type, len(self.data))


class Entry(object):
    """一个对话条目。"""

    __slots__ = ("extra", "instructions", "offset")

    def __init__(self, extra=0):
        self.extra = extra
        self.instructions = []
        self.offset = 0

    def texts(self):
        return [i for i in self.instructions if i.is_text]

    def lines(self, encoding="cp932"):
        """返回 ``[(指令下标, 说话人, 文本)]``。

        说话人按最近的 ``7/8/9`` 指令跟踪：
        ``player``（自机）/ ``boss``（敌机）/ ``none``（旁白）/ ``None``（未指定）。
        """
        out = []
        speaker = None
        for j, ins in enumerate(self.instructions):
            if ins.type == SPEAKER_PLAYER:
                speaker = "player"
            elif ins.type == SPEAKER_BOSS:
                speaker = "boss"
            elif ins.type == SPEAKER_NONE:
                speaker = "none"
            elif ins.is_text:
                out.append((j, speaker, ins.text(encoding)))
        return out

    def __repr__(self):
        return "<Entry extra=%d instrs=%d>" % (
            self.extra, len(self.instructions))


def _parse_stream(data, start, end):
    """把 [start, end) 解析成指令列表。"""
    instrs = []
    pos = start
    while pos + 4 <= end:
        time, type_, length = struct.unpack_from("<HBB", data, pos)
        if pos + 4 + length > end:
            break
        instrs.append(Instruction(
            time, type_, bytes(data[pos + 4:pos + 4 + length])))
        pos += 4 + length
    return instrs


class MsgFile(object):
    """解析后的 .msg 文件。"""

    def __init__(self, encoding="cp932"):
        self.entries = []
        self.encoding = encoding

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    @classmethod
    def from_bytes(cls, data, encoding="cp932"):
        if len(data) < 4:
            raise MsgError("文件过小")
        entry_count = struct.unpack_from("<I", data, 0)[0]
        if entry_count > 100000:
            raise MsgError("条目数量异常: %d" % entry_count)
        table_end = 4 + entry_count * 8
        if table_end > len(data):
            raise MsgError("条目表被截断")

        raw = []
        for i in range(entry_count):
            off, extra = struct.unpack_from("<ii", data, 4 + i * 8)
            raw.append((off, extra))

        obj = cls(encoding)
        bounds = [off for off, _ in raw] + [len(data)]
        for i in range(entry_count):
            start = raw[i][0]
            end = bounds[i + 1]
            entry = Entry(raw[i][1])
            entry.offset = start
            if 0 <= start <= end <= len(data):
                entry.instructions = _parse_stream(data, start, end)
            obj.entries.append(entry)
        return obj

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_bytes(self):
        """重建 .msg 文件（重新计算条目偏移）。"""
        body = bytearray()
        offsets = []
        table_end = 4 + len(self.entries) * 8
        for entry in self.entries:
            offsets.append(table_end + len(body))
            for instr in entry.instructions:
                if len(instr.data) > 255:
                    raise MsgError("指令数据过长（%d 字节）" % len(instr.data))
                body += struct.pack("<HBB", instr.time & 0xFFFF,
                                    instr.type & 0xFF, len(instr.data))
                body += instr.data
        header = bytearray(struct.pack("<I", len(self.entries)))
        for off, entry in zip(offsets, self.entries):
            header += struct.pack("<ii", off, entry.extra)
        return bytes(header + body)

    # ------------------------------------------------------------------
    # 便捷接口
    # ------------------------------------------------------------------
    def plain_text(self):
        """返回 [(条目号, 指令号, 文本)]。"""
        out = []
        for i, entry in enumerate(self.entries):
            for j, instr in enumerate(entry.instructions):
                if instr.is_text:
                    out.append((i, j, instr.text(self.encoding)))
        return out


def _cjk_ratio(s):
    if not s:
        return 0.0
    cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return cjk / float(len(s))

def _bad_ratio(s):
    if not s:
        return 0.0
    bad = sum(1 for ch in s
              if ch == "\ufffd" or "\uff61" <= ch <= "\uff9f")
    return bad / float(len(s))


def guess_encoding(data):
    """猜测 .msg 文本编码：优先 GBK（汉化版），否则 Shift-JIS。"""
    best = None
    for enc in ("cp932", "gbk"):
        try:
            msg = MsgFile.from_bytes(data, enc)
        except MsgError:
            continue
        sample = "".join(t for _, _, t in msg.plain_text()[:60])
        score = _cjk_ratio(sample) - _bad_ratio(sample)
        if best is None or score > best[0]:
            best = (score, enc)
    return best[1] if best else "cp932"


# ----------------------------------------------------------------------
# 对话文档（导出 / 导入）
# ----------------------------------------------------------------------
#: 文档里每行的格式：  [条目号.指令号] [说话人] 文本
#: 第二个方括号内容不固定（可能是 自机/敌机/旁白，也可能是角色名），
#: 导入时只有命中已知说话人标签才会被剥离。
DOC_LINE_RE = re.compile(
    r"^\s*\[(\d+)\.(\d+)\]\s*(?:\[([^\]]*)\])?\s?(.*)$")


def export_document(msgfile, filename, encoding=None):
    """把对话导出成可编辑的文本（UTF-8），供翻译/校对使用。"""
    encoding = encoding or msgfile.encoding
    info = describe_file(filename)
    labels = speaker_labels(filename)
    head = []
    head.append("# 东方星莲船 对话文档")
    head.append("# 文件: %s" % filename)
    meta = []
    if info["player"]:
        meta.append("角色: %s" % info["player"])
    if info["stage"]:
        meta.append("关卡: %s" % info["stage"])
    if info["boss"]:
        meta.append("Boss: %s" % info["boss"])
    if info["scene"]:
        meta.append("场景: %s" % info["scene"])
    head.append("# " + "    ".join(meta) if meta else "#")
    head.append("# 编码: %s" % encoding)
    head.append("#")
    head.append("# 修改方法：只改每行 [条目.指令] 后面的文字，"
                "保存后用工具的「导入文档」写回。")
    head.append("# 行首的 [%s] 只是提示，删掉也不影响导入。" %
                "]/[".join([labels["player"], labels["boss"], labels["none"]]))
    head.append("# " + "=" * 58)

    lines = list(head)
    for ei, entry in enumerate(msgfile.entries):
        lines.append("")
        lines.append("# ---- 条目 %d (id=%d) ----" % (ei, entry.extra))
        for ji, speaker, text in entry.lines(encoding):
            tag = labels.get(speaker, "")
            lines.append("[%d.%d] [%s] %s" % (ei, ji, tag, text))
    return "\n".join(lines) + "\n"


def import_document(text, msgfile, encoding=None, dry_run=False):
    """把编辑过的文档写回 ``msgfile``。

    :param dry_run: 为真时只计算差异，不修改 ``msgfile``。
    :return: ``(修改条数, 未匹配行列表, 无法编码的字符列表, 差异列表)``
             差异列表元素为 ``(条目号, 指令号, 原文, 新文)``。
    """
    encoding = encoding or msgfile.encoding
    labels = _label_set()
    changed = 0
    unmatched = []
    bad_chars = []
    changes = []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = DOC_LINE_RE.match(line)
        if not m:
            unmatched.append(line)
            continue
        ei, ji = int(m.group(1)), int(m.group(2))
        new_text = m.group(4)
        # 第二个方括号只有确实是说话人标签时才剥离
        if m.group(3) is not None and m.group(3) not in labels:
            new_text = "[%s] %s" % (m.group(3), new_text)
        if ei >= len(msgfile.entries):
            unmatched.append(line)
            continue
        entry = msgfile.entries[ei]
        if ji >= len(entry.instructions):
            unmatched.append(line)
            continue
        ins = entry.instructions[ji]
        if not ins.is_text:
            unmatched.append(line)
            continue
        old_text = ins.text(encoding)
        if old_text != new_text:
            changes.append((ei, ji, old_text, new_text))
            for ch in check_encoding(new_text, encoding):
                if ch not in bad_chars:
                    bad_chars.append(ch)
            if not dry_run:
                ins.set_text(new_text, encoding)
            changed += 1
    return changed, unmatched, bad_chars, changes
