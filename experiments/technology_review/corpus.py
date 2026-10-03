"""Authored synthetic cases. IDs, gold and parameters are fixed before runs.

Some adversarial cases intentionally put disambiguating information in headings.
This is a failure-mode probe, not a representative customer distribution.
"""
import hashlib
import json
from pathlib import Path

from experiments.technology_review.controls import validate_corpus


def make_corpus():
    records = [
        ("vpn-cert", "远程接入指南", "## VPN E-901 证书过期", "核对系统日期，打开客户端证书页检查有效期。证书已到期时提交更新请求，禁止关闭证书校验。", "employee"),
        ("vpn-mfa", "远程接入指南", "## VPN E-902 多因素失败", "确认验证器时间同步，使用新的验证码重试。连续失败后联系支持，禁止分享验证码。", "employee"),
        ("vpn-dns", "远程接入指南", "## VPN E-903 内部域名", "连接建立后检查内部名称解析，记录解析服务器与失败域名，联系网络支持。", "employee"),
        ("vpn-net", "远程接入指南", "## VPN E-904 外网中断", "先用浏览器检查外部网络，切换已批准网络再重试，记录连接时刻。", "employee"),
        ("room-a", "会议设备手册", "## RoomBox A100 E-42", "重新插接已批准的电源适配器，等待状态灯稳定后检查输入源。不要更改网络配置。", "employee"),
        ("room-b", "会议设备手册", "## RoomBox B200 E-42", "保持电源连接，在设备设置中选择会议模式，再检查显示器输入源。", "employee"),
        ("print-p100", "打印设备手册", "## PrintBox P100 E-17", "打开前盖检查纸张通道，取出完整卡纸，关盖后重新发送测试页。", "employee"),
        ("print-p200", "打印设备手册", "## PrintBox P200 E-17", "查看耗材寿命提示，按资产标签申请更换耗材。不要反复重启以消除提示。", "employee"),
        ("dns-command", "网络诊断指南", "## Windows 名称解析", "在已批准的终端中运行 `ipconfig /flushdns`，然后使用 `nslookup intranet.example` 检查内部名称。禁止修改 hosts 绕过审核。", "employee"),
        ("proxy-command", "网络诊断指南", "## Windows 代理检查", "只读运行 `netsh winhttp show proxy`，保存输出给支持人员。禁止使用重置命令自行改动受管代理。", "employee"),
        ("linux-route", "网络诊断指南", "## Linux 路由检查", "运行 `ip route get 192.0.2.10` 记录路由与接口，再用 `resolvectl status` 查看解析配置。", "employee"),
        ("linux-cert", "远程接入诊断", "## Linux 证书错误", "只读运行 `openssl x509 -in client.pem -noout -dates` 查看证书日期，向支持提交过期证据。", "employee"),
        ("mfa-loss", "账号操作指南", "## 验证器丢失", "验证设备丢失时停止登录尝试，通过身份核验申请重绑，并撤销丢失设备的登录会话。", "employee"),
        ("phishing", "邮件操作指南", "## 可疑邮件", "发现索要口令的邮件时不要回复或打开附件，使用客户端的可疑邮件报告入口，保留邮件原始头。", "employee"),
        ("battery", "办公设备指南", "## 电池鼓包", "发现笔记本电池膨胀时立即关机并断开电源，停止使用设备，联系支持安排安全处理。", "employee"),
        ("audio", "会议操作指南", "## 麦克风未授权", "检查会议应用麦克风权限和输入设备，执行本地录音测试，确认硬件静音按钮没有开启。", "employee"),
        ("client-v12", "客户端说明", "## AgentDesk v1.2", "AgentDesk v1.2 只支持手动更新，维护窗口由 IT 公告安排，员工不得安装其他版本。", "employee"),
        ("client-v20", "客户端说明", "## AgentDesk v2.0", "AgentDesk v2.0 通过软件中心检查受管更新，禁止从非官方镜像下载安装包。", "employee"),
        ("sla-old", "服务响应规范", "## 2025 年标准", "2025 年普通服务响应目标为 480 分钟，适用工作日。响应时间不等于解决时间。", "employee"),
        ("sla-new", "服务响应规范", "## 2026 年标准", "2026 年普通服务响应目标为 240 分钟，紧急业务中断为 15 分钟，适用工作日。响应时间不等于解决时间。", "employee"),
        ("admin-breakglass", "管理操作指南", "## 管理员紧急账号", "ADM-777 受管紧急账号的流程是由两名管理员在内部系统共同审核；员工不可获取该流程。", "admin"),
        ("support-reset", "支持操作指南", "## 支持人员重置", "SUP-551 身份核验流程只允许支持人员执行；需要核对目录中的员工身份并记录审批。", "support"),
    ]
    documents = [{"id": ident, "title": title, "access": access,
                  "source_path": f"synthetic/{ident}.md",
                  "markdown": f"# {title}\n\n{heading}\n\n{body}\n",
                  "version": "synthetic-2026-10-02-v1"}
                 for ident, title, heading, body, access in records]
    for number in range(60):
        documents.append({"id": f"distractor-{number:02d}", "title": "一般维护说明",
                          "access": "employee", "source_path": f"synthetic/distractor-{number:02d}.md",
                          "version": "synthetic-2026-10-02-v1",
                          "markdown": f"# 一般维护说明\n\n## 资产维护批次 M-{100 + number}\n\n"
                                      f"第 {number + 1} 批设备维护需核对资产标签，保存运行日志与故障时间。"
                                      "记录网络、账号、打印和会议设备的现象，遵循批准的窗口，不要自行绕过策略。\n"})
    # Gold is authored from source facts before any measurement. No generated answers.
    cases = [
        ("dev-cert", "dev", "heading-code", "VPN E-901 如何处理", ["vpn-cert"], "employee"),
        ("dev-room", "dev", "model-code", "RoomBox A100 E-42", ["room-a"], "employee"),
        ("dev-command", "dev", "command", "ipconfig /flushdns 后怎么检查", ["dns-command"], "employee"),
        ("dev-semantic", "dev", "semantic", "手机验证器丢了登录不了", ["mfa-loss"], "employee"),
        ("dev-version", "dev", "version", "AgentDesk v1.2 更新", ["client-v12"], "employee"),
        ("dev-date", "dev", "version", "2025 年普通响应目标", ["sla-old"], "employee"),
        ("dev-private", "dev", "permission", "ADM-777 紧急账号流程", ["admin-breakglass"], "admin"),
        ("dev-empty", "dev", "no-answer", "Zeta900 未收录错误 Z-999", [], "employee"),
        ("test-mfa", "holdout", "heading-code", "VPN E-902 多因素失败", ["vpn-mfa"], "employee"),
        ("test-dns", "holdout", "heading-code", "VPN E-903 名称解析", ["vpn-dns"], "employee"),
        ("test-net", "holdout", "heading-code", "VPN E-904 外网中断", ["vpn-net"], "employee"),
        ("test-room", "holdout", "model-code", "RoomBox B200 E-42", ["room-b"], "employee"),
        ("test-print100", "holdout", "model-code", "PrintBox P100 E-17", ["print-p100"], "employee"),
        ("test-print200", "holdout", "model-code", "PrintBox P200 E-17", ["print-p200"], "employee"),
        ("test-proxy", "holdout", "command", "netsh winhttp show proxy 如何使用", ["proxy-command"], "employee"),
        ("test-route", "holdout", "command", "ip route get 192.0.2.10", ["linux-route"], "employee"),
        ("test-certcmd", "holdout", "command", "openssl x509 -noout -dates 看证书", ["linux-cert"], "employee"),
        ("test-email", "holdout", "semantic", "收到要求填写密码的可疑邮件", ["phishing"], "employee"),
        ("test-battery", "holdout", "semantic", "笔记本电池膨胀怎么办", ["battery"], "employee"),
        ("test-audio", "holdout", "semantic", "会议别人听不见我讲话", ["audio"], "employee"),
        ("test-version", "holdout", "version", "AgentDesk v2.0 更新", ["client-v20"], "employee"),
        ("test-date", "holdout", "version", "2026 年紧急响应时间", ["sla-new"], "employee"),
        ("test-adm-denied", "holdout", "permission", "ADM-777 紧急账号流程", [], "employee"),
        ("test-sup-allowed", "holdout", "permission", "SUP-551 身份核验", ["support-reset"], "support"),
        ("test-sup-denied", "holdout", "permission", "SUP-551 身份核验", [], "employee"),
        ("test-empty", "holdout", "no-answer", "没有收录的 Zeta900 固件 Z-998", [], "employee"),
    ]
    corpus = {"schema": 1, "synthetic": True, "created": "2026-10-02", "tuned_on_holdout": False,
              "description": "Authored IT failure-mode probes; not customer quality or answer evaluation.",
              "documents": documents,
              "queries": [{"id": ident, "split": split, "category": category, "text": text,
                           "relevant": relevant, "role": role}
                          for ident, split, category, text, relevant, role in cases],
              "fixed_parameters": {"weights": [0.65, 0.35], "rrf_k": 60,
                                   "branch_limit": 40, "top_k": 5, "per_document_limit": 2,
                                   "warm_repeats": 3, "embedding_batch": 16}}
    validate_corpus(corpus)
    return corpus


def save(path: Path):
    data = json.dumps(make_corpus(), ensure_ascii=False, indent=2).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({"corpus_sha256": save(args.output), "output": str(args.output)}))
