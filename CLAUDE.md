任务要求是开发 MS graph 的 MCP server， 实现 MCP 的 http-sse 协议，不做任何本地协议。

框架使用 python + uv + fastMCP 

需要创建 Dockerfile 并且支持 curl health 健康检测。 

通过 MCP Header 传递必要认证参数。

注意: Graph API 用的是 OAuth 授权，但本 MCP 服务不需要处理OAuth 逻辑，只需要从 Header 中获取 AccessToken 直接使用即可。  OAuth 过程由调用方处理，你无需操心。

需要实现的 Tool 包含但不限于以下部分。
查重 graph_check_user_exists(第4步)Microsoft Graph / EntraUser.Read.All 或 Directory.Read.All
建号 graph_create_user(第5步)Microsoft Graph / EntraUser.ReadWrite.All
分组 graph_assign_groups(第6步)Microsoft Graph / EntraGroupMember.ReadWrite.All(部分组需 Group.ReadWrite.All)
查许可库存 graph_check_license_stock(第7步)Microsoft Graph / EntraOrganization.Read.All(读 subscribedSkus)
配许可 graph_assign_license(第7步)Microsoft Graph / EntraUser.ReadWrite.All(+建号时已设 usage_location)