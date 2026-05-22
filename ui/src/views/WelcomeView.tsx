export function WelcomeView({ project_id }: { project_id?: string }) {
  return (
    <div className="empty-state">
      <h3>Welcome to AIPC Conductor</h3>
      <p>
        {project_id
          ? `Project "${project_id}" selected. Choose a session from the sidebar to view tasks.`
          : 'Select a project from the sidebar to get started, or create a new one.'}
      </p>
    </div>
  );
}
