/**
 * useMe — decode the stored internal JWT to get the current user's
 * email, role, and name. No network call needed.
 *
 * Returns: { email, role, name, isAdmin, isOwner(ownerEmail) }
 */
export function useMe() {
  const token = localStorage.getItem('tf_token')
  if (!token) return { email: null, role: null, name: null, isAdmin: false, isOwner: () => false }

  try {
    const parts = token.split('.')
    if (parts.length < 2) throw new Error('bad jwt')
    const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')))
    const email = payload.email || null
    const role  = payload.role  || 'viewer'
    const name  = payload.name  || email || 'User'
    const isAdmin = role === 'admin'
    const isOwner = (ownerEmail) =>
      !!email && !!ownerEmail && email === ownerEmail

    return { email, role, name, isAdmin, isOwner }
  } catch {
    return { email: null, role: null, name: null, isAdmin: false, isOwner: () => false }
  }
}
