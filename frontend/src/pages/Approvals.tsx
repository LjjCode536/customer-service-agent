import { Button, Space, Table, Tag, Typography, message } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { useApprovals, useDecideApproval } from '../api/queries'
import type { Approval } from '../api/types'

export default function Approvals() {
  const approvals = useApprovals()
  const decide = useDecideApproval()

  async function submit(id: string, status: 'approved' | 'rejected') {
    try {
      await decide.mutateAsync({ id, status })
      message.success('操作成功')
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          Human Review
        </Typography.Title>
        <Button icon={<ReloadOutlined />} onClick={() => void approvals.refetch()} loading={approvals.isFetching}>
          刷新
        </Button>
      </Space>
      <Table
        rowKey="id"
        loading={approvals.isLoading}
        dataSource={approvals.data ?? []}
        columns={[
          { title: '审批', dataIndex: 'id' },
          { title: '订单', dataIndex: 'order_id' },
          { title: '角色', dataIndex: 'approver_role' },
          { title: '顺序', dataIndex: 'sequence_no' },
          { title: '状态', dataIndex: 'status', render: (value: string) => <Tag>{value}</Tag> },
          {
            title: '操作',
            render: (_: unknown, row: Approval) => (
              <Space>
                <Button
                  disabled={row.status !== 'pending' || decide.isPending}
                  onClick={() => void submit(row.id, 'approved')}
                >
                  通过
                </Button>
                <Button
                  danger
                  disabled={row.status !== 'pending' || decide.isPending}
                  onClick={() => void submit(row.id, 'rejected')}
                >
                  拒绝
                </Button>
              </Space>
            ),
          },
        ]}
      />
    </>
  )
}
