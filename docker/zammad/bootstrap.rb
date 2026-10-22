# Runs once after zammad-init (`rails r /bootstrap/bootstrap.rb`, see docker-compose.yml): finishes the getting-started
# wizard, creates the finance-ops groups, an admin, an agent the hub acts as, a customer that reconciliation tickets
# are filed for, and an API token for the agent written to /secrets/zammad_token (a bind mount of ./.secrets).
# Idempotent: re-running rotates the token and leaves everything else as it is.

GROUPS = ['Finance Ops', 'Billing', 'Collections', 'Support'].freeze

Setting.set('system_init_done', true)
Setting.set('fqdn', '127.0.0.1:8081')
Setting.set('http_type', 'http')
Setting.set('organization', 'Ledgerline Inc.')

GROUPS.each do |name|
  Group.find_by(name: name) || Group.create!(name: name, active: true, updated_by_id: 1, created_by_id: 1)
end

def ensure_user(login, attrs)
  user = User.find_by(login: login)
  return user.tap { |u| u.update!(attrs.except(:password)) } if user

  User.create!(attrs.merge(login: login, email: login, active: true, updated_by_id: 1, created_by_id: 1))
end

admin = ensure_user('admin@finops.local',
                    firstname: 'Finance', lastname: 'Admin', password: ENV.fetch('ZAMMAD_ADMIN_PASSWORD'),
                    roles: Role.where(name: %w[Admin Agent]))
hub = ensure_user('integration-hub@finops.local',
                  firstname: 'Integration', lastname: 'Hub', roles: Role.where(name: 'Agent'))
ensure_user('reconciliation@finops.local',
            firstname: 'Reconciliation', lastname: 'Run', roles: Role.where(name: 'Customer'))

access = GROUPS.to_h { |g| [g, 'full'] }
[admin, hub].each { |u| u.update!(group_names_access_map: access) }

Token.where(user_id: hub.id, action: 'api').destroy_all
token = Token.create!(action: 'api', persistent: true, user_id: hub.id, name: 'finops-integration-hub',
                      preferences: { permission: ['ticket.agent'] })

path = '/secrets/zammad_token'
File.write(path, token.token)
File.chmod(0o644, path) # read by the hub container (another uid); local folder only, see ADR 0005
puts "zammad bootstrapped: groups #{GROUPS.join(', ')}; agent ##{hub.id}; token written to #{path}"
