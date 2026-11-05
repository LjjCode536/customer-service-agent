CUSTOMERS=[
 {'id':'U1001','user_name':'李明','vip_level':'normal','account_age_days':420,'recent_ticket_count':0,'recent_refund_count':0,'complaint_count_30d':0},
 {'id':'U1002','user_name':'王丽','vip_level':'normal','account_age_days':240,'recent_ticket_count':1,'recent_refund_count':0,'complaint_count_30d':0},
 {'id':'U1003','user_name':'赵先生','vip_level':'vip','account_age_days':980,'recent_ticket_count':1,'recent_refund_count':1,'complaint_count_30d':0},
 {'id':'U1004','user_name':'陈女士','vip_level':'normal','account_age_days':600,'recent_ticket_count':2,'recent_refund_count':1,'complaint_count_30d':0},
 {'id':'U1005','user_name':'周先生','vip_level':'vip','account_age_days':1200,'recent_ticket_count':3,'recent_refund_count':1,'complaint_count_30d':2},
]
ORDERS=[
 {'id':'O10001','user_id':'U1001','amount':699,'status':'shipped','payment_status':'paid','shipment_status':'in_transit','tracking_no':'SF10001','product_type':'electronics','created_at':'2026-09-11T09:00:00','delivered_at':None},
 {'id':'O10002','user_id':'U1002','amount':399,'status':'paid','payment_status':'paid','shipment_status':'not_shipped','tracking_no':None,'product_type':'electronics','created_at':'2026-09-14T09:00:00','delivered_at':None},
 {'id':'O10003','user_id':'U1003','amount':12999,'status':'paid','payment_status':'paid','shipment_status':'not_shipped','tracking_no':None,'product_type':'electronics','created_at':'2026-09-14T10:00:00','delivered_at':None},
 {'id':'O10004','user_id':'U1004','amount':899,'status':'paid','payment_status':'paid','shipment_status':'not_shipped','tracking_no':None,'product_type':'electronics','created_at':'2026-09-13T10:00:00','delivered_at':None},
 {'id':'O10005','user_id':'U1005','amount':1599,'status':'delivered','payment_status':'paid','shipment_status':'delivered','tracking_no':'YT10005','product_type':'appliance','created_at':'2026-09-01T10:00:00','delivered_at':'2026-09-05T18:00:00'},
]
LOGISTICS=[{'order_id':'O10001','status':'in_transit','last_event':'上海分拨中心','last_update_hours':38,'updated_at':'2026-09-13T18:00:00'},{'order_id':'O10005','status':'delivered','last_event':'已签收','last_update_hours':0,'updated_at':'2026-09-05T18:00:00'}]
KNOWLEDGE=[
 {'id':'KB-REFUND-V2','category':'refund','product_type':'electronics','title':'电子商品售后退款政策 V2','version':'2025-v2','status':'inactive','effective_from':'2025-01-01','content':'旧版政策：未发货订单可申请退款；签收后5天内未拆封可退。','keywords':['退款','未发货','签收','5天']},
 {'id':'KB-REFUND-V3','category':'refund','product_type':'electronics','title':'电子商品售后退款政策 V3','version':'2026-v3','status':'active','effective_from':'2026-01-01','content':'当前政策：未发货且已支付订单可全额退款；电子商品签收7天内且不影响二次销售可申请退货退款。高金额、重复退款或异常账户必须人工审核。','keywords':['退款','未发货','已支付','7天','高金额','重复退款','人工审核']},
 {'id':'KB-LOGISTICS-V1','category':'logistics','product_type':'all','title':'物流异常处理SOP','version':'2026-v1','status':'active','effective_from':'2026-01-01','content':'运输中物流超过24小时无更新视为异常，创建P2物流工单；VIP强投诉可升级P1并转投诉专席。','keywords':['物流','24小时','异常','P2','VIP','投诉']},
 {'id':'KB-INVOICE-V1','category':'invoice','product_type':'all','title':'电子发票开具说明','version':'2026-v1','status':'active','effective_from':'2026-01-01','content':'订单完成后可在订单详情申请电子发票，企业抬头需填写税号。','keywords':['发票','电子发票','税号']},
]
MOCK_CASES=[
 {'case_key':'logistics_stale','title':'物流38小时未更新','user_id':'U1001','message':'订单 O10001 物流两天没更新，到底怎么回事？'},
 {'case_key':'low_refund','title':'399元未发货低风险退款','user_id':'U1002','message':'订单 O10002 还没发货，我不想要了，帮我退款。'},
 {'case_key':'high_refund','title':'12999元高额退款人工审核','user_id':'U1003','message':'订单 O10003 还没发货，12999元帮我直接退掉。'},
 {'case_key':'duplicate_refund','title':'重复退款申请拦截','user_id':'U1004','message':'订单 O10004 再帮我退一次款。'},
 {'case_key':'vip_complaint','title':'VIP重复售后强投诉','user_id':'U1005','message':'订单 O10005 的东西坏第三次了，再不给我解决我要投诉！'},
 {'case_key':'faq_invoice','title':'普通FAQ','user_id':'U1001','message':'电子发票怎么开？'},
]
