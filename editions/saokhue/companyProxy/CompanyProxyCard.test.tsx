import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CompanyProxyStatus } from './api'
import { CompanyProxyCard } from './CompanyProxyCard'

const api = vi.hoisted(() => ({
  status: vi.fn(),
  save: vi.fn(),
  test: vi.fn(),
  restartGateway: vi.fn(),
}))

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  companyProxyApi: api,
}))

const off: CompanyProxyStatus = {
  enabled: false,
  url: '',
  username: '',
  no_proxy: '',
  has_password: false,
  auth: 'auto',
  windows_available: true,
  restart_required: false,
  running: null,
  not_started: '',
}

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CompanyProxyCard />
    </QueryClientProvider>,
  )
}

async function openDialog() {
  const user = userEvent.setup()
  renderCard()
  await user.click(await screen.findByTestId('company-proxy-card'))
  await screen.findByRole('dialog')
  return user
}

beforeEach(() => {
  vi.resetAllMocks()
  api.status.mockResolvedValue(off)
})

describe('CompanyProxyCard', () => {
  it('shows Off and opens the dialog with the saved values', async () => {
    api.status.mockResolvedValue({ ...off, url: 'http://proxy.corp.test:8080', username: 'alice' })
    renderCard()
    expect(await screen.findByText('Off')).toBeTruthy()
    await userEvent.setup().click(screen.getByTestId('company-proxy-card'))
    expect(((await screen.findByLabelText('Proxy URL')) as HTMLInputElement).value).toBe('http://proxy.corp.test:8080')
    expect((screen.getByLabelText('User name') as HTMLInputElement).value).toBe('alice')
  })

  it('renders nothing for a caller the owner-only status refuses', async () => {
    api.status.mockRejectedValue(new Error('Only the dashboard owner can manage the company proxy.'))
    const { container } = renderCard()
    await waitFor(() => expect(api.status).toHaveBeenCalled())
    await waitFor(() => expect(container.querySelector('[data-testid="company-proxy-card"]')).toBeNull())
  })

  it('tests the typed settings and shows the outcome', async () => {
    api.test.mockResolvedValue({ ok: false, outcome: 'rejected', status: 407, schemes: ['NTLM'], scheme: 'Basic' })
    const user = await openDialog()
    await user.type(screen.getByLabelText('Proxy URL'), 'proxy.corp.test:8080')
    await user.type(screen.getByLabelText('User name'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'p@ss')
    await user.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(
      await screen.findByText(
        'The proxy does not take a user name and password; it asks for NTLM. Choose "My Windows account" above.',
      ),
    ).toBeTruthy()
    expect(api.test).toHaveBeenCalledWith({
      url: 'proxy.corp.test:8080',
      username: 'alice',
      auth: 'auto',
      password: 'p@ss',
    })
  })

  it('saves, then offers a restart', async () => {
    api.save.mockResolvedValue({ ...off, enabled: true, url: 'proxy.corp.test:8080', has_password: true, restart_required: true })
    api.restartGateway.mockResolvedValue({})
    const user = await openDialog()
    await user.click(screen.getByRole('switch'))
    await user.type(screen.getByLabelText('Proxy URL'), 'proxy.corp.test:8080')
    await user.type(screen.getByLabelText('User name'), 'alice')
    await user.type(screen.getByLabelText('Password'), 'p@ss')
    await user.type(screen.getByLabelText('Reach directly (no proxy)'), '.corp.test')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Saved. Restart the gateway to apply it.')).toBeTruthy()
    expect(api.save).toHaveBeenCalledWith({
      enabled: true,
      auth: 'auto',
      url: 'proxy.corp.test:8080',
      username: 'alice',
      no_proxy: '.corp.test',
      password: 'p@ss',
    })
    await user.click(screen.getByRole('button', { name: 'Restart now' }))
    await waitFor(() => expect(api.restartGateway).toHaveBeenCalledTimes(1))
  })

  it('keeps a stored password unless it is replaced or removed', async () => {
    api.status.mockResolvedValue({ ...off, enabled: true, url: 'proxy.corp.test:8080', username: 'alice', has_password: true })
    api.save.mockResolvedValue({ ...off })
    const user = await openDialog()
    expect((screen.getByLabelText('Password') as HTMLInputElement).placeholder).toBe(
      'Stored in the vault. Type to replace it.',
    )
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1))
    expect(api.save.mock.calls[0][0]).not.toHaveProperty('password')
    expect(api.save.mock.calls[0][0]).not.toHaveProperty('clear_password')
  })

  it('sends clear_password after Remove stored password', async () => {
    api.status.mockResolvedValue({ ...off, url: 'proxy.corp.test:8080', has_password: true })
    api.save.mockResolvedValue({ ...off })
    const user = await openDialog()
    await user.click(screen.getByRole('button', { name: 'Remove stored password' }))
    expect(screen.getByText('The stored password will be removed when you save.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1))
    expect(api.save.mock.calls[0][0]).toMatchObject({ clear_password: true })
  })

  it('signs in as the Windows account: no user fields, auth sent on test and save', async () => {
    api.test.mockResolvedValue({ ok: true, outcome: 'ok', status: 200, schemes: [], scheme: 'Negotiate' })
    api.save.mockResolvedValue({ ...off, auth: 'windows' })
    const user = await openDialog()
    await user.click(screen.getByRole('radio', { name: /My Windows account/ }))
    expect(screen.queryByLabelText('User name')).toBeNull()
    expect(screen.queryByLabelText('Password')).toBeNull()
    await user.type(screen.getByLabelText('Proxy URL'), 'proxy.corp.test:8080')
    await user.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText('Connected through the proxy (Negotiate).')).toBeTruthy()
    expect(api.test).toHaveBeenCalledWith({ url: 'proxy.corp.test:8080', username: '', auth: 'windows' })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1))
    expect(api.save.mock.calls[0][0]).toMatchObject({ auth: 'windows', url: 'proxy.corp.test:8080' })
  })

  it('disables the Windows option where Windows sign-in is unavailable', async () => {
    api.status.mockResolvedValue({ ...off, windows_available: false })
    await openDialog()
    expect((screen.getByRole('radio', { name: /My Windows account/ }) as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByText('Signing in with the Windows account works only on Windows.')).toBeTruthy()
  })
})
