#include "plugin_service_impl.h"

#include <algorithm>
#include <exception>
#include <string>

namespace boat::gateway {
namespace {
std::size_t ParseToken(const std::string& token) {
  if (token.empty()) {
    return 0;
  }
  return static_cast<std::size_t>(std::stoull(token));
}

grpc::Status MapPluginException(const std::exception& ex) {
  const std::string message = ex.what();
  if (message.find("not found") != std::string::npos || message.find("missing") != std::string::npos) {
    return grpc::Status(grpc::StatusCode::NOT_FOUND, message);
  }
  if (message.find("invalid") != std::string::npos || message.find("abi") != std::string::npos) {
    return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, message);
  }
  if (message.find("already") != std::string::npos) {
    return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, message);
  }
  return grpc::Status(grpc::StatusCode::INTERNAL, message);
}
}  // namespace

PluginServiceImpl::PluginServiceImpl(GatewayContext& ctx) : ctx_(ctx) {}

grpc::Status PluginServiceImpl::RegisterPlugin(grpc::ServerContext*, const boat::v1::RegisterPluginRequest* request,
                                               boat::v1::PluginResponse* response) {
  try {
    const std::string config = request->config_json().empty()
                                   ? "{}" : request->config_json();
    const auto handle = ctx_.sim.plugin_manager().Load(request->path(), config);
    auto* plugin = response->mutable_plugin();
    plugin->set_plugin_id(handle.name);
    plugin->set_name(handle.name);
    plugin->set_version(std::to_string(handle.abi_version));
    plugin->set_loaded(true);
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapPluginException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected register plugin error");
  }
}

grpc::Status PluginServiceImpl::ListPlugins(grpc::ServerContext*, const boat::v1::ListPluginsRequest* request,
                                            boat::v1::ListPluginsResponse* response) {
  try {
    const auto plugins = ctx_.sim.plugin_manager().List();
    const std::size_t offset = ParseToken(request->page().page_token());
    const std::size_t page_size = request->page().page_size() == 0 ? plugins.size() : request->page().page_size();
    const std::size_t end = std::min(plugins.size(), offset + page_size);
    for (std::size_t i = offset; i < end; ++i) {
      auto* plugin = response->add_plugins();
      plugin->set_plugin_id(plugins[i]);
      plugin->set_name(plugins[i]);
      plugin->set_version("unknown");
      plugin->set_loaded(true);
    }
    response->mutable_page()->set_total_size(static_cast<std::uint32_t>(plugins.size()));
    if (end < plugins.size()) {
      response->mutable_page()->set_next_page_token(std::to_string(end));
    }
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapPluginException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected list plugins error");
  }
}

grpc::Status PluginServiceImpl::GetPluginInfo(grpc::ServerContext*, const boat::v1::GetPluginInfoRequest* request,
                                              boat::v1::PluginResponse* response) {
  try {
    const auto plugins = ctx_.sim.plugin_manager().List();
    const auto it = std::find(plugins.begin(), plugins.end(), request->plugin_id());
    if (it == plugins.end()) {
      return grpc::Status(grpc::StatusCode::NOT_FOUND, "plugin not found");
    }
    auto* plugin = response->mutable_plugin();
    plugin->set_plugin_id(*it);
    plugin->set_name(*it);
    plugin->set_version("unknown");
    plugin->set_loaded(true);
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapPluginException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected get plugin info error");
  }
}

grpc::Status PluginServiceImpl::UnloadPlugin(grpc::ServerContext*, const boat::v1::UnloadPluginRequest* request,
                                             boat::v1::UnloadPluginResponse* response) {
  try {
    ctx_.sim.plugin_manager().Unload(request->plugin_id());
    response->set_unloaded(true);
    return grpc::Status::OK;
  } catch (const std::exception& ex) {
    return MapPluginException(ex);
  } catch (...) {
    return grpc::Status(grpc::StatusCode::INTERNAL, "unexpected unload plugin error");
  }
}

}  // namespace boat::gateway
