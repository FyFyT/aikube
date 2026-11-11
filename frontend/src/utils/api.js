import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' }
})

export const clusterAPI = {
  getPods:        (ns)  => api.get(`/cluster/pods${ns ? `?namespace=${ns}` : ''}`),
  getNodes:       ()    => api.get('/cluster/nodes'),
  getDeployments: (ns)  => api.get(`/cluster/deployments${ns ? `?namespace=${ns}` : ''}`),
  getServices:    (ns)  => api.get(`/cluster/services${ns ? `?namespace=${ns}` : ''}`),
  getNamespaces:  ()    => api.get('/cluster/namespaces'),
  getPodLogs:     (ns, name, tail=100) => api.get(`/cluster/pods/${ns}/${name}/logs?tail=${tail}`),
  deletePod:      (ns, name) => api.delete(`/cluster/pods/${ns}/${name}`),
  deleteDeployment: (ns, name) => api.delete(`/cluster/deployments/${ns}/${name}`),
  scaleDeployment:  (ns, name, replicas) => api.patch(`/cluster/deployments/${ns}/${name}/scale`, { replicas }),
  restartDeployment:(ns, name) => api.post(`/cluster/deployments/${ns}/${name}/restart`),
  createDeployment: (data) => api.post('/cluster/deployments', data),
  createNamespace:  (name) => api.post('/cluster/namespaces', { name }),
  deleteNamespace:  (name) => api.delete(`/cluster/namespaces/${name}`),
  applyYaml:        (yaml, ns) => api.post('/cluster/apply-yaml', { yaml, namespace: ns }),
}

export const aiAPI = {
  generateYAML: (description, model='groq') => api.post('/ai/generate-yaml', { description, model }),
  detect:       () => api.get('/ai/detect'),
  chat:         (message, history=[], model='groq') => api.post('/ai/chat', { message, history, model }),
}

export const securityAPI = {
  scan:               () => api.get('/security/audit/scan'),
  getRoles:           () => api.get('/security/rbac/roles'),
  getBindings:        () => api.get('/security/rbac/bindings'),
  getNetworkPolicies: () => api.get('/security/network-policies'),
  getSecrets:         () => api.get('/security/secrets'),
}

export const devopsAPI = {
  getMetrics: () => api.get('/devops/metrics/cluster'),
  getEvents:  (ns) => api.get(`/devops/events${ns ? `?namespace=${ns}` : ''}`),
  getHealth:  () => api.get('/devops/health'),
}

export const mlAPI = {
  getMetrics:          () => api.get('/ml/metrics'),
  scan:                () => api.get('/ml/scan'),
  getFeatureImportance:() => api.get('/ml/feature-importance'),
  getConfusionMatrix:  () => api.get('/ml/confusion-matrix'),
  getRocCurve:         () => api.get('/ml/roc-curve'),
  kubebench:           () => api.get('/ml/kubebench'),
  retrain:             () => api.post('/ml/retrain'),
}

export default api
