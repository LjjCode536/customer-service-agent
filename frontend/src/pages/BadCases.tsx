import {useEffect,useState} from 'react';
import {Button,Form,Input,Modal,Space,Table,Tag,Typography,message} from 'antd';
import {api} from '../api/client';

type BadCase={id:number;trace_id?:string;query:string;error_type:string;root_cause:string;expected_result:any;fixed:boolean};

export default function BadCases(){
  const[rows,setRows]=useState<BadCase[]>([]);
  const[open,setOpen]=useState(false);
  const[form]=Form.useForm();

  function load(){api.get('/bad-cases').then(r=>setRows(r.data))}
  useEffect(load,[]);

  async function create(){
    const values=await form.validateFields();
    let expected_result={};
    if(values.expected_result){
      try{expected_result=JSON.parse(values.expected_result)}
      catch{message.error('期望结果必须是合法 JSON');return}
    }
    await api.post('/bad-cases',{trace_id:values.trace_id||null,query:values.query,error_type:values.error_type,root_cause:values.root_cause||'',expected_result});
    message.success('Bad Case 已创建');
    setOpen(false);form.resetFields();load();
  }

  async function markFixed(id:number){
    await api.patch(`/bad-cases/${id}`,{fixed:true});
    message.success('已标记修复');
    load();
  }

  return <>
    <Space style={{width:'100%',justifyContent:'space-between',marginBottom:16}}>
      <Typography.Title level={3} style={{margin:0}}>Bad Case Center</Typography.Title>
      <Button type="primary" onClick={()=>setOpen(true)}>登记 Bad Case</Button>
    </Space>
    <Table rowKey="id" dataSource={rows} pagination={false}
      expandable={{expandedRowRender:(r:BadCase)=><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify({expected_result:r.expected_result,root_cause:r.root_cause},null,2)}</pre>}}
      columns={[
        {title:'Trace',dataIndex:'trace_id',render:(x?:string)=>x||'-'},
        {title:'Query',dataIndex:'query'},
        {title:'错误类型',dataIndex:'error_type'},
        {title:'根因',dataIndex:'root_cause',render:(x:string)=>x||'-'},
        {title:'状态',dataIndex:'fixed',render:(x:boolean)=>x?<Tag color="green">fixed</Tag>:<Tag color="orange">open</Tag>},
        {title:'操作',render:(_:unknown,r:BadCase)=>r.fixed?'-':<Button size="small" onClick={()=>markFixed(r.id)}>标记已修复</Button>},
      ]}/>
    <Modal title="登记 Bad Case" open={open} onOk={create} onCancel={()=>setOpen(false)} okText="创建">
      <Form form={form} layout="vertical">
        <Form.Item name="trace_id" label="Trace ID"><Input placeholder="trace_xxx"/></Form.Item>
        <Form.Item name="query" label="失败 Query" rules={[{required:true,message:'请输入 Query'}]}><Input.TextArea rows={3}/></Form.Item>
        <Form.Item name="error_type" label="错误类型" rules={[{required:true,message:'请输入错误类型'}]}><Input placeholder="wrong_approval_chain"/></Form.Item>
        <Form.Item name="root_cause" label="根因"><Input.TextArea rows={2}/></Form.Item>
        <Form.Item name="expected_result" label="期望结果（JSON）"><Input.TextArea rows={3} placeholder='{"approval_chain":["refund_specialist"]}'/></Form.Item>
      </Form>
    </Modal>
  </>;
}
