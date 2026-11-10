import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  Descriptions,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message as toast,
} from 'antd'
import type { UploadFile, UploadProps } from 'antd'
import { PlusOutlined, RedoOutlined, ReloadOutlined } from '@ant-design/icons'
import { downloadFile } from '../api/client'
import {
  useArchiveDocument,
  useArchiveDocumentVersion,
  useDocument,
  useDocuments,
  useDocumentVersion,
  usePublishDocumentVersion,
  useRestoreDocument,
  useRestoreDocumentVersion,
  useRetryDocumentVersion,
  useUploadDocument,
  useUploadDocumentVersion,
} from '../api/queries'
import type { DocumentSummary, DocumentVersion } from '../api/types'
import { ErrorAlert, QueryEmpty, errorText } from '../components/ListState'
import {
  formatBytes,
  formatDateTime,
  matchesKeyword,
  statusColor,
  uniqueOptions,
  versionLabel,
} from '../utils/format'
import { listPagination } from '../utils/table'
import { DEMO_ROLES, useRuntime } from '../store/useRuntime'

const visibilityColor: Record<string, string> = {
  tenant: 'green',
  staff_only: 'orange',
  restricted: 'red',
}

const MAX_UPLOAD_BYTES = 20 * 1024 * 1024
const UPLOAD_FORMATS = ['txt', 'md', 'pdf', 'docx'] as const
const UPLOAD_ACCEPT = UPLOAD_FORMATS.map((extension) => `.${extension}`).join(',')
const UPLOAD_DOTTED_LABEL = UPLOAD_FORMATS.map((extension) => `.${extension}`).join(' / ')
const UPLOAD_FORMAT_LABEL = UPLOAD_FORMATS.join(' / ')
const UPLOAD_PATTERN = new RegExp(`\\.(${UPLOAD_FORMATS.join('|')})$`, 'i')
const VISIBILITY_OPTIONS = ['tenant', 'staff_only', 'restricted'].map((value) => ({
  value,
  label: value,
}))
const ROLE_OPTIONS = DEMO_ROLES.map((value) => ({
  value,
  label: value,
}))

function normFile(event: { fileList: UploadFile[] } | UploadFile[]): UploadFile[] {
  if (Array.isArray(event)) return event
  return event?.fileList ?? []
}

function downloadVersion(documentId: string, version: DocumentVersion) {
  void downloadFile(
    `/documents/${documentId}/versions/${version.id}/download`,
    version.source_filename ?? `${version.id}.txt`,
  )
}

export default function Knowledge() {
  const documents = useDocuments()
  const role = useRuntime((state) => state.role)
  const isManager = role === 'manager'
  const [keyword, setKeyword] = useState('')
  const [category, setCategory] = useState<string>()
  const [status, setStatus] = useState<string>()
  const [productType, setProductType] = useState<string>()
  const [visibility, setVisibility] = useState<string>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [drawerDocumentId, setDrawerDocumentId] = useState<string>()
  const [selectedVersion, setSelectedVersion] = useState<string>()

  const [uploadForm] = Form.useForm()
  const [uploadOpen, setUploadOpen] = useState(false)
  const [uploadTarget, setUploadTarget] = useState<DocumentSummary>()
  const uploadDocument = useUploadDocument()
  const uploadVersion = useUploadDocumentVersion(uploadTarget?.id)
  const retryVersion = useRetryDocumentVersion()
  const publishVersion = usePublishDocumentVersion()
  const archiveVersion = useArchiveDocumentVersion()
  const restoreVersion = useRestoreDocumentVersion()
  const archiveDocument = useArchiveDocument()
  const restoreDocument = useRestoreDocument()
  const [pendingAction, setPendingAction] = useState<string>()
  const uploadVisibility = Form.useWatch('visibility', uploadForm)

  const detail = useDocument(drawerDocumentId, Boolean(drawerDocumentId))
  const versionDetail = useDocumentVersion(
    drawerDocumentId,
    selectedVersion,
    Boolean(selectedVersion),
  )

  const rows = documents.data ?? []
  const categories = useMemo(() => uniqueOptions(rows.map((row) => row.category)), [rows])
  const statuses = useMemo(() => uniqueOptions(rows.map((row) => row.status)), [rows])
  const productTypes = useMemo(() => uniqueOptions(rows.map((row) => row.product_type)), [rows])
  const visibilities = useMemo(() => uniqueOptions(rows.map((row) => row.visibility)), [rows])
  const hasPendingVersions = Boolean(
    detail.data?.versions.some(
      (version) => version.status === 'draft' || version.status === 'processing',
    ),
  )

  const filtered = useMemo(
    () =>
      rows.filter((row) => {
        if (category && row.category !== category) return false
        if (status && row.status !== status) return false
        if (productType && row.product_type !== productType) return false
        if (visibility && row.visibility !== visibility) return false
        return matchesKeyword(keyword, [
          row.id,
          row.title,
          row.category,
          row.product_type,
          row.visibility,
          row.published_version?.label,
        ])
      }),
    [rows, category, status, productType, visibility, keyword],
  )

  useEffect(() => {
    setPage(1)
  }, [keyword, category, status, productType, visibility])

  function resetFilters() {
    setKeyword('')
    setCategory(undefined)
    setStatus(undefined)
    setProductType(undefined)
    setVisibility(undefined)
  }

  function closeDrawer() {
    setDrawerDocumentId(undefined)
    setSelectedVersion(undefined)
  }

  function openUpload(target?: DocumentSummary) {
    uploadForm.resetFields()
    uploadForm.setFieldsValue({ visibility: 'tenant', force: false })
    setUploadTarget(target)
    setUploadOpen(true)
  }

  function closeUpload() {
    setUploadOpen(false)
    setUploadTarget(undefined)
    uploadForm.resetFields()
  }

  const beforeUpload: UploadProps['beforeUpload'] = (file) => {
    if (!UPLOAD_PATTERN.test(file.name)) {
      toast.error(`仅支持 ${UPLOAD_DOTTED_LABEL} 文件`)
      return Upload.LIST_IGNORE
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.error('单文件上限 20MB')
      return Upload.LIST_IGNORE
    }
    // Keep the selected file in the form; submitUpload performs the multipart POST.
    return false
  }

  async function submitUpload() {
    let values
    try {
      values = await uploadForm.validateFields()
    } catch {
      return
    }
    const fileList = (values.file as UploadFile[] | undefined) ?? []
    const file = fileList[0]?.originFileObj
    if (!file) {
      toast.error(`请选择 ${UPLOAD_FORMAT_LABEL} 文件`)
      return
    }

    const data = new FormData()
    data.append('file', file)
    data.append('force', values.force ? 'true' : 'false')
    if (values.effective_from) data.append('effective_from', values.effective_from)
    if (values.label) data.append('label', values.label)

    if (uploadTarget) {
      try {
        const response = await uploadVersion.mutateAsync(data)
        toast.success(`新版本已提交（202）：${response.version_id}，正在解析`)
        closeUpload()
        setDrawerDocumentId(response.document_id)
      } catch (error) {
        toast.error(errorText(error))
      }
      return
    }

    data.append('title', values.title)
    if (values.category) data.append('category', values.category)
    if (values.product_type) data.append('product_type', values.product_type)
    data.append('visibility', values.visibility ?? 'tenant')
    if (values.visibility === 'restricted') {
      for (const roleName of (values.allowed_roles as string[] | undefined) ?? []) {
        data.append('allowed_roles', roleName)
      }
    }

    try {
      const response = await uploadDocument.mutateAsync(data)
      toast.success(`文档已提交（202）：${response.version_id}，正在解析`)
      closeUpload()
      setSelectedVersion(undefined)
      setDrawerDocumentId(response.document_id)
    } catch (error) {
      toast.error(errorText(error))
    }
  }

  async function retryFailed(documentId: string, version: DocumentVersion) {
    try {
      await retryVersion.mutateAsync({ documentId, versionId: version.id })
      toast.success('已重新入队，Worker 将再次解析')
    } catch (error) {
      toast.error(errorText(error))
    }
  }

  async function runVersionAction(
    action: 'publish' | 'archive' | 'restore',
    documentId: string,
    version: DocumentVersion,
  ) {
    const key = `${action}:${version.id}`
    setPendingAction(key)
    try {
      if (action === 'publish') {
        await publishVersion.mutateAsync({ documentId, versionId: version.id })
        toast.success(
          version.status === 'superseded'
            ? `已回滚到 ${versionLabel(version)}`
            : `已发布 ${versionLabel(version)}`,
        )
      } else if (action === 'archive') {
        await archiveVersion.mutateAsync({ documentId, versionId: version.id })
        toast.success(`已下架 ${versionLabel(version)}，该版本退出召回`)
      } else {
        await restoreVersion.mutateAsync({ documentId, versionId: version.id })
        toast.success(`${versionLabel(version)} 已恢复为 ready，可重新发布`)
      }
    } catch (error) {
      toast.error(errorText(error))
    } finally {
      setPendingAction(undefined)
    }
  }

  function confirmArchiveVersion(documentId: string, version: DocumentVersion) {
    Modal.confirm({
      title: `下架版本 ${versionLabel(version)}？`,
      content: '下架后该版本立即退出召回，历史与原始文件保留，可恢复到 ready 后重新发布。',
      okText: '下架',
      okButtonProps: { danger: true },
      onOk: () => runVersionAction('archive', documentId, version),
    })
  }

  async function runDocumentAction(action: 'archive' | 'restore', documentId: string) {
    const key = `document:${action}:${documentId}`
    setPendingAction(key)
    try {
      if (action === 'archive') {
        await archiveDocument.mutateAsync(documentId)
        toast.success('文档已归档，所有版本退出召回')
      } else {
        await restoreDocument.mutateAsync(documentId)
        toast.success('文档已恢复，原 published 版本重新具备召回资格')
      }
    } catch (error) {
      toast.error(errorText(error))
    } finally {
      setPendingAction(undefined)
    }
  }

  function confirmArchiveDocument(documentId: string) {
    Modal.confirm({
      title: '归档整个文档？',
      content: '归档后该文档所有版本退出召回，版本状态保持不变；可随时恢复文档。',
      okText: '归档',
      okButtonProps: { danger: true },
      onOk: () => runDocumentAction('archive', documentId),
    })
  }

  const hasFilters = Boolean(keyword || category || status || productType || visibility)

  return (
    <>
      <Space style={{ width: '100%', justifyContent: 'space-between', marginBottom: 16 }} align="start">
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>
            Knowledge Center
          </Typography.Title>
          <Typography.Text type="secondary">
            文档目录按分类与文档 ID 排列；manager 可上传 {UPLOAD_FORMAT_LABEL}，提交后由 Worker 异步解析。
          </Typography.Text>
        </div>
        <Space>
          <Typography.Text type="secondary">
            共 {filtered.length} / {rows.length} 个文档
          </Typography.Text>
          <Button icon={<ReloadOutlined />} onClick={() => void documents.refetch()} loading={documents.isFetching}>
            刷新
          </Button>
          {isManager ? (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => openUpload()}>
              上传文档
            </Button>
          ) : null}
        </Space>
      </Space>

      {documents.isError ? (
        <ErrorAlert title="文档目录加载失败" error={documents.error} onRetry={() => void documents.refetch()} />
      ) : null}

      <Space wrap style={{ marginBottom: 16 }}>
        <Input
          allowClear
          placeholder="搜索标题 / 分类 / 版本"
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          style={{ width: 260 }}
        />
        <Select
          allowClear
          placeholder="分类"
          value={category}
          onChange={setCategory}
          style={{ width: 150 }}
          options={categories.map((value) => ({ value, label: value }))}
        />
        <Select
          allowClear
          placeholder="产品类型"
          value={productType}
          onChange={setProductType}
          style={{ width: 150 }}
          options={productTypes.map((value) => ({ value, label: value }))}
        />
        <Select
          allowClear
          placeholder="Visibility"
          value={visibility}
          onChange={setVisibility}
          style={{ width: 150 }}
          options={visibilities.map((value) => ({ value, label: value }))}
        />
        <Select
          allowClear
          placeholder="状态"
          value={status}
          onChange={setStatus}
          style={{ width: 130 }}
          options={statuses.map((value) => ({ value, label: value }))}
        />
        <Button onClick={resetFilters} disabled={!hasFilters}>
          重置过滤
        </Button>
      </Space>

      <Table
        rowKey="id"
        loading={documents.isLoading}
        dataSource={filtered}
        locale={{
          emptyText: (
            <QueryEmpty
              isError={documents.isError}
              loading={documents.isLoading}
              filtered={hasFilters}
              emptyText="暂无文档。执行 seed 后会写入 FAQ 演示文档与版本。"
              filteredText="没有匹配的文档，试试调整过滤条件。"
            />
          ),
        }}
        pagination={listPagination(page, pageSize, setPage, setPageSize)}
        scroll={{ x: 1080 }}
        columns={[
          {
            title: '文档',
            dataIndex: 'title',
            width: 280,
            render: (value: string | null | undefined, row: DocumentSummary) => (
              <Space orientation="vertical" size={0}>
                <Typography.Text strong>{value || '-'}</Typography.Text>
                <Typography.Text type="secondary" copyable style={{ fontSize: 12 }}>
                  {row.id}
                </Typography.Text>
              </Space>
            ),
          },
          { title: '分类', dataIndex: 'category', width: 110, render: (value?: string) => value || '-' },
          { title: '产品', dataIndex: 'product_type', width: 110, render: (value?: string) => value || '-' },
          {
            title: '可见性',
            dataIndex: 'visibility',
            width: 120,
            render: (value: string) => <Tag color={visibilityColor[value] ?? 'default'}>{value}</Tag>,
          },
          {
            title: '当前发布版本',
            dataIndex: 'published_version',
            width: 150,
            render: (version?: DocumentVersion | null) =>
              version ? <Tag color="blue">{versionLabel(version)}</Tag> : '-',
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 100,
            render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag>,
          },
          {
            title: '版本数',
            dataIndex: 'version_count',
            width: 80,
            render: (value: number) => value ?? 0,
          },
          {
            title: '更新时间',
            dataIndex: 'updated_at',
            width: 180,
            render: (value?: string | null) => formatDateTime(value),
          },
          {
            title: '操作',
            width: 120,
            render: (_: unknown, row: DocumentSummary) => (
              <Button size="small" onClick={() => setDrawerDocumentId(row.id)}>
                版本抽屉
              </Button>
            ),
          },
        ]}
      />

      <Drawer
        title="文档版本"
        width={820}
        open={Boolean(drawerDocumentId)}
        onClose={closeDrawer}
        extra={
          detail.data && isManager ? (
            <Space>
              {detail.data.status === 'active' ? (
                <>
                  <Button
                    size="small"
                    icon={<PlusOutlined />}
                    onClick={() => openUpload(detail.data)}
                  >
                    上传新版本
                  </Button>
                  <Button
                    size="small"
                    danger
                    loading={pendingAction === `document:archive:${detail.data.id}`}
                    onClick={() => confirmArchiveDocument(detail.data!.id)}
                  >
                    归档文档
                  </Button>
                </>
              ) : (
                <Button
                  size="small"
                  loading={pendingAction === `document:restore:${detail.data.id}`}
                  onClick={() => void runDocumentAction('restore', detail.data!.id)}
                >
                  恢复文档
                </Button>
              )}
            </Space>
          ) : null
        }
      >
        {detail.isError ? (
          <ErrorAlert title="文档详情加载失败" error={detail.error} onRetry={() => void detail.refetch()} />
        ) : null}
        {detail.data ? (
          <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="文档" span={2}>
                {detail.data.title || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="文档 ID">{detail.data.id}</Descriptions.Item>
              <Descriptions.Item label="分类">{detail.data.category || '-'}</Descriptions.Item>
              <Descriptions.Item label="产品类型">{detail.data.product_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="Visibility">
                <Tag color={visibilityColor[detail.data.visibility] ?? 'default'}>{detail.data.visibility}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="文档状态">
                <Tag color={statusColor(detail.data.status)}>{detail.data.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="当前发布版本">
                {detail.data.published_version
                  ? versionLabel(detail.data.published_version)
                  : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="更新时间" span={2}>
                {formatDateTime(detail.data.updated_at)}
              </Descriptions.Item>
            </Descriptions>

            {detail.data.status === 'archived' ? (
              <Alert
                type="warning"
                showIcon
                title="文档已归档，所有版本退出召回；恢复文档后原 published 版本重新具备召回资格。"
              />
            ) : null}

            {hasPendingVersions ? (
              <Alert
                type="info"
                showIcon
                title="解析处理中，状态每 2 秒自动刷新；失败后可在这里直接重试。"
              />
            ) : null}

            <Table
              rowKey="id"
              size="small"
              dataSource={detail.data.versions}
              pagination={false}
              columns={[
                {
                  title: '版本',
                  dataIndex: 'label',
                  width: 120,
                  render: (_: unknown, row: DocumentVersion) => (
                    <Space orientation="vertical" size={0}>
                      <Tag color="blue">{versionLabel(row)}</Tag>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {row.id}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '状态 / 失败原因',
                  dataIndex: 'status',
                  width: 200,
                  render: (value: string, row: DocumentVersion) => (
                    <Space orientation="vertical" size={0}>
                      <Tag color={statusColor(value)}>{value}</Tag>
                      {row.error_code ? <Tag color="red">{row.error_code}</Tag> : null}
                      {row.error_message ? (
                        <Typography.Text
                          type="danger"
                          style={{ fontSize: 12 }}
                          ellipsis={{ tooltip: row.error_message }}
                        >
                          {row.error_message}
                        </Typography.Text>
                      ) : null}
                    </Space>
                  ),
                },
                {
                  title: '生效时间',
                  dataIndex: 'effective_from',
                  width: 150,
                  render: (value?: string | null) => {
                    const future = value
                      ? new Date(value).getTime() > Date.now()
                      : false
                    return (
                      <Space size={4} wrap>
                        <span>{value || '-'}</span>
                        {future ? <Tag color="orange">未生效</Tag> : null}
                      </Space>
                    )
                  },
                },
                {
                  title: '发布时间',
                  dataIndex: 'published_at',
                  width: 160,
                  render: (value?: string | null) => formatDateTime(value),
                },
                {
                  title: '原文件',
                  width: 160,
                  render: (_: unknown, row: DocumentVersion) => (
                    <Space orientation="vertical" size={0}>
                      <Typography.Text style={{ fontSize: 12 }}>{row.source_filename || '-'}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {formatBytes(row.size_bytes)}
                      </Typography.Text>
                    </Space>
                  ),
                },
                {
                  title: '操作',
                  width: 330,
                  render: (_: unknown, row: DocumentVersion) => (
                    <Space size={4} wrap>
                      <Button size="small" onClick={() => setSelectedVersion(row.id)}>
                        查看正文
                      </Button>
                      <Button
                        size="small"
                        disabled={!row.has_source_file}
                        onClick={() => downloadVersion(detail.data!.id, row)}
                      >
                        下载
                      </Button>
                      {isManager && detail.data?.status === 'active' && row.status === 'ready' ? (
                        <Button
                          size="small"
                          type="primary"
                          loading={pendingAction === `publish:${row.id}`}
                          onClick={() => void runVersionAction('publish', detail.data!.id, row)}
                        >
                          发布
                        </Button>
                      ) : null}
                      {isManager && detail.data?.status === 'active' && row.status === 'superseded' ? (
                        <Button
                          size="small"
                          loading={pendingAction === `publish:${row.id}`}
                          onClick={() => void runVersionAction('publish', detail.data!.id, row)}
                        >
                          发布（回滚）
                        </Button>
                      ) : null}
                      {isManager && detail.data?.status === 'active' && row.status === 'published' ? (
                        <Button
                          size="small"
                          danger
                          loading={pendingAction === `archive:${row.id}`}
                          onClick={() => confirmArchiveVersion(detail.data!.id, row)}
                        >
                          下架
                        </Button>
                      ) : null}
                      {isManager && detail.data?.status === 'active' && row.status === 'archived' ? (
                        <Button
                          size="small"
                          loading={pendingAction === `restore:${row.id}`}
                          onClick={() => void runVersionAction('restore', detail.data!.id, row)}
                        >
                          恢复
                        </Button>
                      ) : null}
                      {isManager && row.status === 'failed' ? (
                        <Button
                          size="small"
                          danger
                          icon={<RedoOutlined />}
                          loading={retryVersion.isPending}
                          onClick={() => void retryFailed(detail.data!.id, row)}
                        >
                          重试
                        </Button>
                      ) : null}
                    </Space>
                  ),
                },
              ]}
            />
          </Space>
        ) : null}
      </Drawer>

      <Modal
        title={uploadTarget ? `上传新版本 · ${uploadTarget.title ?? uploadTarget.id}` : '上传文档'}
        open={uploadOpen}
        onOk={() => void submitUpload()}
        confirmLoading={uploadTarget ? uploadVersion.isPending : uploadDocument.isPending}
        onCancel={closeUpload}
        okText="提交"
        width={620}
      >
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            title="提交后立即返回 202；解析与 embedding 由 Worker 异步完成。"
          />
          <Form form={uploadForm} layout="vertical" initialValues={{ visibility: 'tenant', force: false }}>
            <Form.Item
              name="file"
              label="文件"
              valuePropName="fileList"
              getValueFromEvent={normFile}
              rules={[{ required: true, message: `请选择 ${UPLOAD_FORMAT_LABEL} 文件` }]}
            >
              <Upload accept={UPLOAD_ACCEPT} maxCount={1} beforeUpload={beforeUpload}>
                <Button icon={<PlusOutlined />}>选择 {UPLOAD_FORMAT_LABEL} 文件</Button>
              </Upload>
            </Form.Item>

            {!uploadTarget ? (
              <>
                <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
                  <Input maxLength={255} />
                </Form.Item>
                <Form.Item name="category" label="分类">
                  <Input placeholder="refund / invoice / logistics" maxLength={64} />
                </Form.Item>
                <Form.Item name="product_type" label="产品类型">
                  <Input placeholder="electronics / all" maxLength={64} />
                </Form.Item>
                <Form.Item name="visibility" label="可见性">
                  <Select options={VISIBILITY_OPTIONS} />
                </Form.Item>
                {uploadVisibility === 'restricted' ? (
                  <Form.Item
                    name="allowed_roles"
                    label="允许角色"
                    rules={[{ required: true, message: 'restricted 需要选择角色' }]}
                  >
                    <Select mode="multiple" options={ROLE_OPTIONS} />
                  </Form.Item>
                ) : null}
              </>
            ) : null}

            <Form.Item name="effective_from" label="生效时间">
              <Input placeholder="2026-01-01 / 留空为今天" />
            </Form.Item>
            <Form.Item name="label" label="版本标签">
              <Input placeholder="2026-v1" maxLength={64} />
            </Form.Item>
            <Form.Item name="force" valuePropName="checked">
              <Checkbox>允许同租户重复内容（force）</Checkbox>
            </Form.Item>
          </Form>
        </Space>
      </Modal>

      <Modal
        title={selectedVersion ? `版本正文 · ${selectedVersion}` : '版本正文'}
        open={Boolean(selectedVersion)}
        onCancel={() => setSelectedVersion(undefined)}
        footer={null}
        width={760}
        loading={versionDetail.isLoading}
      >
        {versionDetail.isError ? (
          <ErrorAlert title="版本正文加载失败" error={versionDetail.error} onRetry={() => void versionDetail.refetch()} />
        ) : null}
        {versionDetail.data ? (
          <Space orientation="vertical" size="small" style={{ width: '100%' }}>
            <Space wrap size={4}>
              <Tag color="blue">{versionLabel(versionDetail.data)}</Tag>
              <Tag color={statusColor(versionDetail.data.status)}>{versionDetail.data.status}</Tag>
              {versionDetail.data.error_code ? <Tag color="red">{versionDetail.data.error_code}</Tag> : null}
              <Button
                size="small"
                disabled={!versionDetail.data.has_source_file}
                onClick={() => downloadVersion(detail.data!.id, versionDetail.data!)}
              >
                下载原文件
              </Button>
              {isManager && versionDetail.data.status === 'failed' ? (
                <Button
                  size="small"
                  danger
                  icon={<RedoOutlined />}
                  loading={retryVersion.isPending}
                  onClick={() => void retryFailed(detail.data!.id, versionDetail.data!)}
                >
                  重试
                </Button>
              ) : null}
            </Space>
            {versionDetail.data.error_message ? (
              <Typography.Text type="danger">{versionDetail.data.error_message}</Typography.Text>
            ) : null}
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
              {versionDetail.data.content_text || '（暂无正文，解析完成后可查看）'}
            </Typography.Paragraph>
          </Space>
        ) : null}
      </Modal>
    </>
  )
}
