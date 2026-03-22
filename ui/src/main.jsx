import React from 'react'
import ReactDOM from 'react-dom/client'
import { Auth0Provider } from '@auth0/auth0-react'
import App from './App.jsx'

const domain   = import.meta.env.VITE_AUTH0_DOMAIN   || 'tokenflow.us.auth0.com'
const clientId = import.meta.env.VITE_AUTH0_CLIENT_ID || 'aUzCxuMq5qToHnSZHCIIGiWgIIb3A32I'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Auth0Provider
      domain={domain}
      clientId={clientId}
      authorizationParams={{
        redirect_uri: window.location.origin,
        scope: 'openid profile email',
      }}
      cacheLocation="localstorage"
    >
      <App />
    </Auth0Provider>
  </React.StrictMode>
)
