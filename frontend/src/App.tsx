import { useEffect, useState } from 'react'
import {
  DashboardOutlined,
  CustomerServiceOutlined,
  UnorderedListOutlined,
  DollarOutlined,
  SafetyCertificateOutlined,
  BookOutlined,
  FileSearchOutlined,
  ExperimentOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Layout, Menu, Select, Segmented, Space, Tag, Typography } from 'antd'
import { Link, Route, Routes, useLocation } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useRuntimeInfo } from './api/queries'
import { DEMO_ROLES, DEMO_TENANTS, DEMO_USERS, useRuntime } from './store/useRuntime'
import Dashboard from './pages/Dashboard'
import AgentLab from './pages/AgentLab'
import Tickets from './pages/Tickets'
import Refunds from './pages/Refunds'
import Approvals from './pages/Approvals'
import Knowledge from './pages/Knowledge'
import Traces from './pages/Traces'
import Evaluation from './pages/Evaluation'
import BadCases from './pages/BadCases'

const { Sider, Header, Content } = Layout

const menus = [
  ['/', <DashboardOutlined />, '运行总览'],
  ['/agent', <CustomerServiceOutlined />, '客服 Agent'],
  ['/tickets', <UnorderedListOutlined />, 'Ticket Center'],
  ['/refunds', <DollarOutlined />, 'Refund Center'],
  ['/approvals', <SafetyCertificateOutlined />, 'Human Review'],
  ['/knowledge', <BookOutlined />, 'Knowledge Center'],
  ['/traces', <FileSearchOutlined />, 'Trace Explorer'],
  ['/evaluation', <ExperimentOutlined />, 'Evaluation'],
  ['/bad-cases', <WarningOutlined />, 'Bad Case'],
]

export default function App() {
  const [collapsed, setCollapsed] = useState(false)
  const location = useLocation()
  const runtimeQuery = useRuntimeInfo()
  const runtime = runtimeQuery.data
  const { user, tenant, role, mode, setUser, setTenant, setRole, setMode } = useRuntime()
  const queryClient = useQueryClient()

  // Security Context is part of every request; switching it invalidates cached views.
  useEffect(() => {
    void queryClient.invalidateQueries()
  }, [user, tenant, role, mode, queryClient])

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed}>
        <div style={{ height: 64, display: 'grid', placeItems: 'center', color: '#fff', fontWeight: 900 }}>
          SERVICE AI
        </div>
        <Menu
          theme="dark"
          selectedKeys={[location.pathname]}
          items={menus.map(([path, icon, label]) => ({
            key: path as string,
            icon,
            label: <Link to={path as string}>{label}</Link>,
          }))}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            background: '#fff',
            padding: '0 24px',
            borderBottom: '1px solid #eee',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 16,
            flexWrap: 'wrap',
            height: 'auto',
            minHeight: 64,
          }}
        >
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>
              企业智能客服 / 工单 Agent Console
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {runtime ? `${runtime.database_type} · ${runtime.workflow_engine} · ${runtime.models.chat}` : '连接运行时…'}
            </Typography.Text>
          </div>
          <Space wrap size={8}>
            <Tag color={runtime?.mock_mode ? 'blue' : 'green'}>
              {runtime?.mock_mode ? 'MOCK CHAT' : 'REAL CHAT'}
            </Tag>
            <Tag color={runtime?.langgraph_available ? 'purple' : 'default'}>
              {runtime?.langgraph_available ? 'LangGraph' : 'Fallback'}
            </Tag>
            <Select
              size="small"
              value={user}
              style={{ width: 96 }}
              onChange={setUser}
              options={DEMO_USERS.map((value) => ({ value, label: value }))}
            />
            <Select
              size="small"
              value={tenant}
              style={{ width: 150 }}
              onChange={setTenant}
              options={DEMO_TENANTS.map((value) => ({ value, label: value }))}
            />
            <Select
              size="small"
              value={role}
              style={{ width: 110 }}
              onChange={setRole}
              options={DEMO_ROLES.map((value) => ({ value, label: value }))}
            />
            <Segmented
              size="small"
              value={mode}
              onChange={(value) => setMode(value as 'mock' | 'real')}
              options={[
                { label: 'Mock Case', value: 'mock' },
                { label: 'Real 请求', value: 'real' },
              ]}
            />
          </Space>
        </Header>
        <Content style={{ padding: 24, background: '#f5f7fa' }}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/agent" element={<AgentLab />} />
            <Route path="/tickets" element={<Tickets />} />
            <Route path="/refunds" element={<Refunds />} />
            <Route path="/approvals" element={<Approvals />} />
            <Route path="/knowledge" element={<Knowledge />} />
            <Route path="/traces" element={<Traces />} />
            <Route path="/evaluation" element={<Evaluation />} />
            <Route path="/bad-cases" element={<BadCases />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}
