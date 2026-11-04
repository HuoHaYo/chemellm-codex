# 产品官网维护

官网为 <https://chemellm.online/>，`http` 与 `www` 跳转到该 HTTPS 主域名。网站只发布产品介绍、安装指引和公开发行包，不连接工程内核或项目数据。

## 源文件与构建

- `site/`：中文响应式页面、CSS、JavaScript、SVG 图标与搜索索引入口，无外部字体、CDN 或统计脚本。
- `deploy/website/`：Nginx、发布脚本和证书续期 systemd 单元。
- `scripts/build_website.py`：只打包明确列出的公开文件及经 Release 校验清单验证的 Windows 安装 ZIP，输出 `output/deployment/website.tar.gz`。

本地预览：

```powershell
python -m http.server 18766 --bind 127.0.0.1 --directory site
```

预览地址为 `http://127.0.0.1:18766/`。安装包由部署构建阶段加入，源码预览不提供 `/downloads/`。页面修改不需要启动内核、数据库或 MCP。

构建：

```powershell
python -X utf8 scripts/build_website.py
```

安装包名称从 `pyproject.toml` 的版本生成，默认读取 `dist/ChemELLM-Codex-0.5.0-windows-x64.zip` 和同目录 `SHA256SUMS.txt`，也可传入 `--installer`、`--checksums`。脚本检查页面版本、下载名称和实际哈希；上线前还需与 GitHub 已发布附件的 digest 核对。更新发行版本时同步修改页面版本和下载链接，不将安装 ZIP 的哈希写回安装包内的构建源码，避免构建输入与产物互相依赖。

## 服务器与发布

服务器已安装 Nginx、Docker 和 `/opt/chemellm-acme/run-certbot.sh`。本机 SSH 配置和公钥指纹保存在被 Git 忽略的 `data/deployment/ssh_config` 与 `known_hosts`，私钥仍使用本机既有 SSH 密钥。旧项目仅作为连接信息的迁移来源；当前站点运行和后续维护不依赖旧仓库。不要提交 SSH 本机配置、私钥、运行数据或标准原文。

每次选择新的 `YYYYMMDD-HHMMSS` 部署标识，先在服务器创建独立目录，再上传、解包及发布。例如将下面的 `20261104-170100` 替换为新的标识，不要复用已有版本：

```powershell
ssh -F data/deployment/ssh_config chemellm-server 'mkdir -p /opt/chemellm-site-deploy/20261104-170100'
scp -F data/deployment/ssh_config output/deployment/website.tar.gz chemellm-server:/opt/chemellm-site-deploy/20261104-170100/website.tar.gz
ssh -F data/deployment/ssh_config chemellm-server 'cd /opt/chemellm-site-deploy/20261104-170100 && tar -xzf website.tar.gz && bash publish.sh 20261104-170100'
```

生产目录为 `/var/www/chemellm-site/releases/<标识>`，`current` 符号链接指向当前版本。脚本校验每个公开文件的 SHA256，备份 Nginx 配置到 `backups/`，检查配置语法，再切换链接并重载。HTTPS 本机探测失败时恢复原配置和已有链接。首次部署需要有效的主域名与 www 证书，并在主机防火墙和云安全组允许 TCP 80/443。

回滚页面时，将 `current` 用临时符号链接和 `mv -Tf` 原子切回指定的已有版本；若需要回滚 Nginx，将选定的备份复制回 `/etc/nginx/conf.d/chemellm.online.conf`，先运行 `nginx -t`，成功后 `systemctl reload nginx`。保留旧版本和备份，确认目标目录后再自行清理。

## HTTPS 与续期

证书路径为 `/etc/letsencrypt/live/chemellm.online/`，覆盖 `chemellm.online` 和 `www.chemellm.online`。首次签发命令：

```sh
/opt/chemellm-acme/run-certbot.sh certonly --webroot --webroot-path /var/www/chemellm-acme --domain chemellm.online --domain www.chemellm.online --cert-name chemellm.online --non-interactive --agree-tos --register-unsafely-without-email
```

`chemellm-site-renew.timer` 每天在服务器 03:00 和 15:00 后随机延迟最多一小时检查续期。服务运行 Certbot，成功后验证并重载 Nginx。ACME 账户沿用现有无邮箱设置。

```sh
systemctl list-timers chemellm-site-renew.timer
journalctl -u chemellm-site-renew.service
/opt/chemellm-acme/run-certbot.sh renew --dry-run --no-random-sleep-on-renew
```

## 上线核对

检查 HTTPS 证书可信、HTTP/www 跳转、静态资源、安装包下载与发布哈希一致；用真实浏览器核对桌面/手机布局、导航、示例复制和 FAQ。页脚备案号为用户提供的 `冀ICP备2026048199号-1`，链接到工信部备案查询网站。详细本次上线验证见 `output/deployment/` 与 `output/playwright/`，这些临时证据不纳入 Git。

2026-11-04 已发布 `20261104-170700`：公网 HTTPS、HTTP/www 跳转、安装包下载哈希、320/390/768/1440 像素屏宽、示例复制、FAQ、手机导航均通过。Nginx 配置验证、续期服务实际运行及两个域名的完整证书续期演练通过；首张证书有效期至 2027-02-02，已启用自动续期定时器。
