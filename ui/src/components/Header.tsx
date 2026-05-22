import type { Route } from '../App';

export function Header({ route }: { route: Route }) {
  return (
    <header className="header">
      <a href="#/" className="header-brand" style={{ color: 'inherit' }}>
        <div className="header-logo" aria-hidden />
        <span>AIPC Conductor</span>
      </a>

      <div className="header-breadcrumb">
        {breadcrumbContent(route)}
      </div>

      <nav className="header-actions">
        <a href="#/configs" className="text-secondary">agent configs</a>
      </nav>
    </header>
  );
}

function breadcrumbContent(route: Route): React.ReactNode {
  if (route.name === 'configs') return <span className="current">Agent configs</span>;
  if (route.name === 'welcome' && route.project_id) {
    return (
      <>
        <span className="sep">/</span>
        <span className="current">{route.project_id}</span>
      </>
    );
  }
  if (route.name === 'tasks') {
    return (
      <>
        <span className="sep">/</span>
        <a href={`#/p/${route.project_id}`}>{route.project_id}</a>
        <span className="sep">/</span>
        <span className="current">{route.session_id}</span>
      </>
    );
  }
  return <span className="text-muted">no project selected</span>;
}
