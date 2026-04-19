# XPath 数据获取工作台

一个基于 `Flask + Jinja2` 的可视化数据获取工具，核心能力是：

- 设置目标网址
- 设置行节点 XPath
- 在表格中逐列配置字段 XPath
- 预览数据结果
- 导出 CSV

另外补了几个实用功能：

- 自定义 `User-Agent`
- 限制最大数据行数
- 浏览器本地记忆上次配置
- 一键载入示例配置

## 运行方式

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python app.py
```

打开浏览器访问：

```text
http://127.0.0.1:5000
```

## 使用建议

推荐优先填写 `行 XPath`，例如：

```text
//div[@class='item']
```

然后每一列使用相对 XPath，例如：

```text
.//a[@class='title']
.//span[@class='price']
```

如果不填写 `行 XPath`，程序会按全局列进行数据获取，再按索引拼成表格。
