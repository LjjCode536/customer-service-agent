import { Table, Tag, Typography } from 'antd'
import { useKnowledge } from '../api/queries'

export default function Knowledge() {
  const knowledge = useKnowledge()

  return (
    <>
      <Typography.Title level={3}>Knowledge Center</Typography.Title>
      <Table
        rowKey="id"
        loading={knowledge.isLoading}
        dataSource={knowledge.data ?? []}
        expandable={{ expandedRowRender: (row) => <div>{row.content}</div> }}
        columns={[
          { title: '标题', dataIndex: 'title' },
          { title: '分类', dataIndex: 'category' },
          { title: '产品', dataIndex: 'product_type' },
          { title: '版本', dataIndex: 'version' },
          {
            title: '状态',
            dataIndex: 'status',
            render: (value: string) => <Tag color={value === 'active' ? 'green' : 'default'}>{value}</Tag>,
          },
          { title: '生效时间', dataIndex: 'effective_from' },
        ]}
      />
    </>
  )
}
