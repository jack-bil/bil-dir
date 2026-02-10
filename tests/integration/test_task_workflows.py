"""Integration tests for task workflows."""
import pytest
import json
import time


class TestTaskCRUD:
    """Test task creation, reading, updating, deletion."""

    def test_create_and_delete_task_workflow(self, client):
        """Should create task, verify it exists, then delete it."""
        # Create task
        response = client.post('/tasks', json={
            'name': 'Test Task',
            'prompt': 'Test prompt',
            'provider': 'codex'
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['ok'] is True
        task_id = data['task']['id']

        # Verify task exists
        response = client.get(f'/tasks/{task_id}')
        assert response.status_code == 200
        data = response.get_json()
        task = data['task']  # GET returns {"task": {...}}
        assert task['name'] == 'Test Task'

        # Delete task
        response = client.delete(f'/tasks/{task_id}')
        assert response.status_code == 200

        # Verify deletion
        response = client.get(f'/tasks/{task_id}')
        assert response.status_code == 404

    def test_update_task_fields(self, client):
        """Should update task name, prompt, schedule."""
        # Create task
        response = client.post('/tasks', json={
            'name': 'Original Name',
            'prompt': 'Original prompt',
            'provider': 'codex'
        })
        task_id = response.get_json()['task']['id']

        # Update name and prompt
        response = client.patch(f'/tasks/{task_id}', json={
            'name': 'Updated Name',
            'prompt': 'Updated prompt'
        })
        assert response.status_code == 200

        # Verify updates
        response = client.get(f'/tasks/{task_id}')
        task = response.get_json()['task']
        assert task['name'] == 'Updated Name'
        assert task['prompt'] == 'Updated prompt'

        # Cleanup
        client.delete(f'/tasks/{task_id}')

    def test_enable_disable_task(self, client):
        """Should toggle task enabled status."""
        # Create task
        response = client.post('/tasks', json={
            'name': 'Toggle Test',
            'prompt': 'Test',
            'provider': 'codex'
        })
        task_id = response.get_json()['task']['id']

        # Disable task
        response = client.patch(f'/tasks/{task_id}', json={'enabled': False})
        assert response.status_code == 200

        # Verify disabled
        task = client.get(f'/tasks/{task_id}').get_json()['task']
        assert task['enabled'] is False

        # Enable task
        response = client.patch(f'/tasks/{task_id}', json={'enabled': True})
        assert response.status_code == 200

        # Verify enabled
        task = client.get(f'/tasks/{task_id}').get_json()['task']
        assert task['enabled'] is True

        # Cleanup
        client.delete(f'/tasks/{task_id}')


class TestScheduleTypes:
    """Test different schedule configurations."""

    def test_manual_schedule(self, client):
        """Should create task with manual schedule."""
        response = client.post('/tasks', json={
            'name': 'Manual Task',
            'prompt': 'Test',
            'schedule': {'type': 'manual'}
        })
        assert response.status_code == 200
        task_id = response.get_json()['task']['id']

        task = client.get(f'/tasks/{task_id}').get_json()['task']
        assert task['schedule']['type'] == 'manual'
        assert task['next_run'] is None

        client.delete(f'/tasks/{task_id}')

    def test_daily_schedule(self, client):
        """Should create task with daily schedule."""
        response = client.post('/tasks', json={
            'name': 'Daily Task',
            'prompt': 'Test',
            'schedule': {'type': 'daily', 'time': '09:00'}
        })
        assert response.status_code == 200
        task_id = response.get_json()['task']['id']

        task = client.get(f'/tasks/{task_id}').get_json()['task']
        assert task['schedule']['type'] == 'daily'
        assert task['schedule']['time'] == '09:00'
        assert task['next_run'] is not None

        client.delete(f'/tasks/{task_id}')

    def test_weekly_schedule(self, client):
        """Should create task with weekly schedule."""
        response = client.post('/tasks', json={
            'name': 'Weekly Task',
            'prompt': 'Test',
            'schedule': {
                'type': 'weekly',
                'time': '14:00',
                'days_of_week': [1, 3, 5]  # Mon, Wed, Fri
            }
        })
        assert response.status_code == 200
        task_id = response.get_json()['task']['id']

        task = client.get(f'/tasks/{task_id}').get_json()['task']
        assert task['schedule']['type'] == 'weekly'
        assert task['schedule']['days_of_week'] == [1, 3, 5]

        client.delete(f'/tasks/{task_id}')

    def test_monthly_schedule(self, client):
        """Should create task with monthly schedule."""
        response = client.post('/tasks', json={
            'name': 'Monthly Task',
            'prompt': 'Test',
            'schedule': {
                'type': 'monthly',
                'day_of_month': 15,
                'time': '10:00',
                'recur_months': 1
            }
        })
        assert response.status_code == 200
        task_id = response.get_json()['task']['id']

        task = client.get(f'/tasks/{task_id}').get_json()['task']
        assert task['schedule']['type'] == 'monthly'
        assert task['schedule']['day_of_month'] == 15

        client.delete(f'/tasks/{task_id}')

    def test_interval_schedule(self, client):
        """Should create task with interval schedule."""
        response = client.post('/tasks', json={
            'name': 'Interval Task',
            'prompt': 'Test',
            'schedule': {
                'type': 'interval',
                'interval_sec': 3600  # 1 hour
            }
        })
        assert response.status_code == 200
        task_id = response.get_json()['task']['id']

        task = client.get(f'/tasks/{task_id}').get_json()['task']
        assert task['schedule']['type'] == 'interval'
        assert task['schedule']['interval_sec'] == 3600

        client.delete(f'/tasks/{task_id}')


class TestOrchestratorWorkflows:
    """Test orchestrator creation and management."""

    def test_create_and_delete_orchestrator(self, client):
        """Should create orchestrator, verify it exists, then delete it."""
        # Create orchestrator
        response = client.post('/orchestrators', json={
            'name': 'Test Orchestrator',
            'goal': 'Test goal',
            'provider': 'claude',
            'managed_sessions': []
        })
        assert response.status_code == 200
        data = response.get_json()
        assert data['ok'] is True
        orch_id = data['orchestrator']['id']

        # Verify exists in list
        response = client.get('/orchestrators')
        assert response.status_code == 200
        data = response.get_json()
        orch_ids = [o['id'] for o in data['orchestrators']]
        assert orch_id in orch_ids

        # Delete
        response = client.delete(f'/orchestrators/{orch_id}')
        assert response.status_code == 200

        # Verify deletion
        response = client.get('/orchestrators')
        data = response.get_json()
        orch_ids = [o['id'] for o in data['orchestrators']]
        assert orch_id not in orch_ids

    def test_pause_and_start_orchestrator(self, client):
        """Should toggle orchestrator enabled status."""
        # Create orchestrator
        response = client.post('/orchestrators', json={
            'name': 'Toggle Test',
            'goal': 'Test',
            'provider': 'claude',
            'managed_sessions': []
        })
        orch_id = response.get_json()['orchestrator']['id']

        # Pause
        response = client.post(f'/orchestrators/{orch_id}/pause')
        assert response.status_code == 200
        assert response.get_json()['ok'] is True

        # Verify paused (check in list)
        response = client.get('/orchestrators')
        orchs = response.get_json()['orchestrators']
        orch = next((o for o in orchs if o['id'] == orch_id), None)
        assert orch is not None
        assert orch['enabled'] is False

        # Start
        response = client.post(f'/orchestrators/{orch_id}/start')
        assert response.status_code == 200
        assert response.get_json()['ok'] is True

        # Verify enabled
        response = client.get('/orchestrators')
        orchs = response.get_json()['orchestrators']
        orch = next((o for o in orchs if o['id'] == orch_id), None)
        assert orch is not None
        assert orch['enabled'] is True

        # Cleanup
        client.delete(f'/orchestrators/{orch_id}')
