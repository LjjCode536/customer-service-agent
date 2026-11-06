import { Button, Space, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useRefunds } from '../api/queries'

export default function Refunds() {
  const refunds = useRefunds()

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Refund Center
        </Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={() => void refunds.refetch()} loading={refunds.isFetching}>
          刷新
        </Button>
      </Space>
      <Table
        rowKey="id"
        loading={refunds.isLoading}
        dataSource={refunds.data ?? []}
        columns={[
          { title: '退款单', dataIndex: 'id' },
          { title: '订单', dataIndex: 'order_id' },
          { title: '用户', dataIndex: 'user_id' },
          { title: '金额', dataIndex: 'amount' },
          { title: '外部退款号', dataIndex: 'external_refund_id' },
          { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color="green">{value}</Tag> },
          { title: '幂等 Key', dataIndex: 'idempotency_key' },
        ]}
      />
    </>
  )
}
