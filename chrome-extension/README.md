# Chrome 连接拾页

1. 在终端进入拾页项目目录，运行 `.venv/bin/python scripts/install_chrome_bridge.py`，安装本机连接器。此操作会写入 Chrome 的 Native Messaging 配置。
2. 在 Chrome 打开 `chrome://extensions`，开启「开发者模式」，点击「加载已解压的扩展程序」，选择项目中的 `chrome-extension` 文件夹。
3. 打开拾页应用，在 Chrome 登录晋江，打开「拾页 · 晋江连接」扩展，点击「连接拾页」。正在下载或同步书架时，请先暂停并等当前操作完成，再连接。

连接器仅读取 `jjwxc.net`、`jjwxc.com` 及其子域的 Cookie，不读取密码或其他网站。登录数据通过 Chrome 本机消息通道和本机回环接口交给拾页，保存在 macOS 钥匙串；不写入扩展存储或普通文件，不上传到其他服务器。拾页必须运行才能接收连接，且会在保存期间阻止启动下载或同步书架。没有找到 Cookie 时需要先登录；是否登录有效由拾页实际读取晋江页面确认。连接保存后，拾页会自动验证登录并同步书架，结果显示在拾页中。

Cookie 读取权限覆盖晋江的 HTTP 与 HTTPS：Chrome 根据 Cookie 的 Secure 属性选择权限校验协议，只声明 HTTPS 会漏掉网站在 HTTPS 页面使用的非 Secure 登录 Cookie。该权限范围不改变拾页通过 HTTPS 访问晋江的方式。

扩展固定 ID：`ebiomijpkjmejjdbpokfcjmdjlopedkk`。`manifest.json` 中的 `key` 是用于固定 ID 的公钥，不是登录密钥。移动项目文件夹后需重新运行安装脚本。

本机连接器运行文件保存在 `~/Library/Application Support/Novelist/ChromeBridge`，使用 Python 基础运行环境，不需要 Chrome 访问项目所在的下载文件夹。更新连接器代码后，需要重新运行安装脚本。

卸载时，在 Chrome 移除此扩展，并删除 `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/local.novelist.connect.json` 和 `~/Library/Application Support/Novelist/ChromeBridge`。该步骤不会删除钥匙串中的登录信息；可在 macOS「钥匙串访问」中查找服务 `local.novelist.jjwxc-session`、账号 `jinjiang` 管理。
