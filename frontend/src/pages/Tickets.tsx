import { Button, Space, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useTickets } from '../api/queries'
import type { Ticket } from '../api/types'

export default function Tickets() {
  const tickets = useTickets()

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Ticket Center
        </Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={() => void tickets.refetch()} loading={tickets.isFetching}>
          刷新
        </Button>
      </Space>
      <Table
        rowKey="id"
        loading={tickets.isLoading}
        dataSource={tickets.data ?? []}
        columns={[
          { title: 'Ticket', dataIndex: 'id' },
          { title: '用户', dataIndex: 'user_id' },
          { title: '订单', dataIndex: 'order_id' },
          { title: '分类', dataIndex: 'category' },
          {
            title: '优先级',
            dataIndex: 'priority',
            render: (value: Ticket['priority']) => (
              <Tag color={value === 'P1' ? 'red' : value === 'P2' ? 'orange' : 'blue'}>{value}</Tag>
            ),
          },
          { title: 'SLA', dataIndex: 'sla_due_at' },
          { title: '处理组', dataIndex: 'assigned_group' },
          { title: '状态', dataIndex: 'status' },
        ]}
      />
    </>
  )
}
