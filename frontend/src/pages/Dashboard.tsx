import { Alert, Button, Card, Col, Empty, Row, Space, Statistic, Table, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import ReactECharts from 'echarts-for-react'
import { useDashboard } from '../api/queries'
import type { SlaRiskItem } from '../api/types'

const priorityColor: Record<string, string> = {
  P0: 'magenta',
  P1: 'red',
  P2: 'orange',
  P3: 'blue',
}

const stateMeta: Record<SlaRiskItem['state'], { color: string; label: string }> = {
  breached: { color: 'red', label: '已超时' },
  at_risk: { color: 'orange', label: '临近超时' },
  on_track: { color: 'green', label: '正常' },
}

function remainingText(item: SlaRiskItem) {
  if (item.state === 'breached') return `超时 ${Math.abs(item.remaining_minutes)} 分钟`
  return `剩余 ${item.remaining_minutes} 分钟`
}

export default function Dashboard() {
  const dashboard = useDashboard()
  const data = dashboard.data
  const priority = data?.ticket_by_priority ?? []
  const category = data?.ticket_by_category ?? []
  const sla = data?.sla_risk

  const priorityOption = {
    tooltip: { trigger: 'axis' },
    grid: { left: 32, right: 16, top: 24, bottom: 24 },
    xAxis: { type: 'category', data: priority.map((item) => item.priority) },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      {
        type: 'bar',
        barWidth: 34,
        data: priority.map((item) => ({
          value: item.count,
          itemStyle: {
            color:
              item.priority === 'P0'
                ? '#eb2f96'
                : item.priority === 'P1'
                  ? '#ff4d4f'
                  : item.priority === 'P2'
                    ? '#fa8c16'
                    : '#1677ff',
          },
        })),
      },
    ],
  }

  const categoryOption = {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, type: 'scroll' },
    series: [
      {
        type: 'pie',
        radius: ['42%', '70%'],
        label: { formatter: '{b}: {c}' },
        data: category.map((item) => ({ name: item.category, value: item.count })),
      },
    ],
  }

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            运行总览
          </Typography.Title>
          <Typography.Text type="secondary">
            工单优先级、退款金额、待审批与 SLA 风险，每 10 秒自动刷新。
          </Typography.Text>
        </div>
        <Space>
          {dashboard.dataUpdatedAt ? (
            <Typography.Text type="secondary">
              更新于 {new Date(dashboard.dataUpdatedAt).toLocaleTimeString()}
            </Typography.Text>
          ) : null}
          <Button icon={<ReloadOutlined />} onClick={() => void dashboard.refetch()} loading={dashboard.isFetching}>
            刷新
          </Button>
        </Space>
      </Space>

      {dashboard.isError ? (
        <Alert type="error" showIcon title="Dashboard 加载失败" description={String(dashboard.error)} />
      ) : null}

      <Row gutter={[16, 16]}>
        <Col xs={12} md={6}>
          <Card loading={dashboard.isLoading}>
            <Statistic title="工单总数" value={data?.tickets_total ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card loading={dashboard.isLoading}>
            <Statistic title="Open Ticket" value={data?.open_tickets ?? 0} styles={{ content: { color: '#fa8c16' } }} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card loading={dashboard.isLoading}>
            <Statistic title="退款金额" prefix="¥" precision={2} value={data?.refund_total ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card loading={dashboard.isLoading}>
            <Statistic
              title="待人工审核"
              value={data?.waiting_approval ?? 0}
              styles={{ content: { color: data?.waiting_approval ? '#cf1322' : undefined } }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card title="工单优先级" loading={dashboard.isLoading}>
            {priority.length ? <ReactECharts option={priorityOption} style={{ height: 280 }} /> : <Empty />}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="工单分类" loading={dashboard.isLoading}>
            {category.length ? <ReactECharts option={categoryOption} style={{ height: 280 }} /> : <Empty />}
          </Card>
        </Col>
      </Row>

      <Card
        style={{ marginTop: 16 }}
        title={
          <Space>
            SLA 风险
            <Tag color="red">超时 {sla?.breached ?? 0}</Tag>
            <Tag color="orange">临近 {sla?.at_risk ?? 0}</Tag>
            <Tag color="green">正常 {sla?.on_track ?? 0}</Tag>
          </Space>
        }
        extra={<Typography.Text type="secondary">临近阈值：{sla?.window_minutes ?? 60} 分钟</Typography.Text>}
        loading={dashboard.isLoading}
      >
        <Table
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={sla?.tickets ?? []}
          locale={{ emptyText: <Empty description="当前没有带 SLA 的 Open Ticket" /> }}
          columns={[
            { title: 'Ticket', dataIndex: 'id' },
            {
              title: '优先级',
              dataIndex: 'priority',
              render: (value: string) => <Tag color={priorityColor[value] ?? 'default'}>{value}</Tag>,
            },
            { title: '分类', dataIndex: 'category' },
            { title: '处理组', dataIndex: 'assigned_group' },
            { title: 'SLA 截止', dataIndex: 'sla_due_at' },
            {
              title: '剩余',
              dataIndex: 'remaining_minutes',
              render: (_: number, item: SlaRiskItem) => remainingText(item),
            },
            {
              title: '状态',
              dataIndex: 'state',
              render: (value: SlaRiskItem['state']) => (
                <Tag color={stateMeta[value].color}>{stateMeta[value].label}</Tag>
              ),
            },
          ]}
        />
      </Card>
    </>
  )
}
