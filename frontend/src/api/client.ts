import axios from 'axios'
export const api=axios.create({baseURL:'/api/v1'})
export const headers=(user='U1001',role='customer')=>({'x-user-id':user,'x-user-role':role,'x-tenant-id':'TENANT-DEMO'})
