bl_info = {
    "name": "GPT-5.2 Chat",
    "author": "Codex",
    "version": (0, 2, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > GPT",
    "description": "Chat with OpenAI models from Blender",
    "category": "3D View",
}

import json
import queue
import threading
import urllib.error
import urllib.request

import bpy


class GPT5AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    api_key: bpy.props.StringProperty(
        name="OpenAI API Key",
        subtype="PASSWORD",
        description="Stored locally in Blender preferences",
        default="",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "api_key")


class GPT5AddonProperties(bpy.types.PropertyGroup):
    system_prompt: bpy.props.StringProperty(
        name="System Prompt",
        description="Optional system guidance for the model",
        default="",
    )
    prompt: bpy.props.StringProperty(
        name="Prompt",
        description="Message to send to the model",
        default="",
    )
    response: bpy.props.StringProperty(
        name="Response",
        description="Last response from the model",
        default="",
        options={'MULTILINE'},
    )
    model: bpy.props.StringProperty(
        name="Model",
        description="OpenAI model name",
        default="gpt-5.2",
    )


class GPT5AddonPanel(bpy.types.Panel):
    bl_label = "GPT-5.2 Chat"
    bl_idname = "VIEW3D_PT_gpt5_chat"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "GPT"

    def draw(self, context):
        layout = self.layout
        props = context.scene.gpt5_addon
        prefs = context.preferences.addons[__name__].preferences

        layout.prop(props, "model")
        layout.prop(props, "system_prompt")
        layout.prop(props, "prompt")
        layout.operator("gpt5.send_message", icon="PLAY")
        layout.separator()
        if not prefs.api_key:
            layout.label(text="Set API key in Add-on Preferences", icon="ERROR")
        layout.label(text="Response")
        layout.prop(props, "response", text="")


class GPT5_OT_SendMessage(bpy.types.Operator):
    bl_idname = "gpt5.send_message"
    bl_label = "Send Message"
    bl_description = "Send the prompt to OpenAI"

    def execute(self, context):
        props = context.scene.gpt5_addon
        prefs = context.preferences.addons[__name__].preferences

        prompt = props.prompt.strip()
        if not prompt:
            self.report({'WARNING'}, "Prompt is empty")
            return {'CANCELLED'}
        if not prefs.api_key.strip():
            self.report({'ERROR'}, "OpenAI API key is missing")
            return {'CANCELLED'}

        props.response = ""
        self._queue = queue.Queue()
        self._done = False
        self._error = None
        self._cancel_event = threading.Event()
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)

        self._thread = threading.Thread(
            target=_stream_openai_response,
            args=(
                self._queue,
                self._cancel_event,
                prefs.api_key.strip(),
                props.model.strip(),
                props.system_prompt.strip(),
                prompt,
            ),
            daemon=True,
        )
        self._thread.start()

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            props = context.scene.gpt5_addon
            while True:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item["type"] == "delta":
                    props.response += item["text"]
                elif item["type"] == "error":
                    self._error = item["message"]
                elif item["type"] == "done":
                    self._done = True

            if self._error:
                props.response = f"Error: {self._error}"
                self.report({'ERROR'}, self._error)
                self._cleanup(context)
                return {'CANCELLED'}
            if self._done:
                self._cleanup(context)
                return {'FINISHED'}

        if event.type in {'ESC'}:
            self._cancel_event.set()
            self._cleanup(context)
            return {'CANCELLED'}

        return {'PASS_THROUGH'}

    def _cleanup(self, context):
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


def _stream_openai_response(queue_out, cancel_event, api_key, model, system_prompt, prompt):
    input_messages = []
    if system_prompt:
        input_messages.append({
            "role": "system",
            "content": [{"type": "text", "text": system_prompt}],
        })
    input_messages.append({
        "role": "user",
        "content": [{"type": "text", "text": prompt}],
    })
    payload = {
        "model": model,
        "input": input_messages,
        "stream": True,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url="https://api.openai.com/v1/responses",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            for raw_line in response:
                if cancel_event.is_set():
                    break
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    queue_out.put({"type": "delta", "text": event.get("delta", "")})
                elif event_type == "response.completed":
                    break
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else ""
        queue_out.put({"type": "error", "message": f"HTTP {exc.code}: {error_body}"})
        queue_out.put({"type": "done"})
        return
    except urllib.error.URLError as exc:
        queue_out.put({"type": "error", "message": f"Network error: {exc.reason}"})
        queue_out.put({"type": "done"})
        return

    queue_out.put({"type": "done"})


classes = (
    GPT5AddonPreferences,
    GPT5AddonProperties,
    GPT5AddonPanel,
    GPT5_OT_SendMessage,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.gpt5_addon = bpy.props.PointerProperty(type=GPT5AddonProperties)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.gpt5_addon


if __name__ == "__main__":
    register()
